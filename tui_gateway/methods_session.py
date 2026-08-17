"""Session / delegation / spawn-tree / billing / pet JSON-RPC handlers (moved verbatim from server.py).

Handler bodies are byte-identical to their pre-split server.py form; they
are rebound onto server.py's globals at install time — see method_ctx.py.
"""

from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


@method("session.create")
def _(rid, params: dict) -> dict:
    cxba_binding = None
    if "cxba_context" in params:
        from tui_gateway.transport import has_cxba_private_authority

        if not has_cxba_private_authority():
            return _err(rid, 4033, "cxba_context requires a trusted CXBA private connection")
        try:
            from tui_gateway.cxba_runtime import validate_session_binding

            cxba_binding = validate_session_binding(params.get("cxba_context"))
        except ValueError as exc:
            return _err(rid, 4034, f"invalid trusted cxba_context: {exc}")
        for live in _sessions.values():
            live_binding = live.get("cxba_binding")
            if (
                isinstance(live_binding, dict)
                and live_binding.get("business_session_id")
                == cxba_binding["business_session_id"]
                and live_binding.get("business_branch_id")
                == cxba_binding["business_branch_id"]
            ):
                return _err(rid, 4092, "business branch is already bound")
        with _profile_db(params) as binding_db:
            if binding_db is None or not hasattr(binding_db, "list_sessions_rich"):
                return _err(rid, 5008, "session store cannot verify business branch binding")
            from tui_gateway.cxba_runtime import binding_from_model_config

            for stored in binding_db.list_sessions_rich(
                limit=10_000,
                include_children=True,
                include_archived=True,
            ):
                stored_binding = binding_from_model_config(stored.get("model_config"))
                if (
                    stored_binding is not None
                    and stored_binding["business_session_id"]
                    == cxba_binding["business_session_id"]
                    and stored_binding["business_branch_id"]
                    == cxba_binding["business_branch_id"]
                ):
                    return _err(rid, 4092, "business branch is already bound")
    sid = uuid.uuid4().hex[:8]
    key = _new_session_key()
    cols = int(params.get("cols", 80))
    history = _coerce_seed_history(params.get("messages"))
    title = str(params.get("title") or "").strip()
    # When set, this is a branch: the new chat copies an existing conversation's
    # history and links back to it so list_sessions_rich keeps it visible and the
    # sidebar can nest it under its parent. Mirrors the TUI /branch marker.
    parent_session_id = str(params.get("parent_session_id") or "").strip() or None
    # Did the client pick a workspace, or are we falling back to the gateway's
    # launch directory? Only an explicit choice is persisted as the session's
    # workspace (see _ensure_session_db_row); otherwise it lands in "No
    # workspace" instead of whatever folder the desktop launched in.
    raw_cwd = str(params.get("cwd") or "").strip()
    try:
        explicit_cwd = bool(raw_cwd) and os.path.isdir(os.path.abspath(os.path.expanduser(raw_cwd)))
    except Exception:
        explicit_cwd = False
    resolved_cwd = _completion_cwd(params)
    source = _resolve_session_source(str(params.get("source") or "").strip() or None)
    _enable_gateway_prompts()

    # ``profile`` (app-global remote mode): a new chat started under a non-launch
    # profile must build its agent + persist against THAT profile's home/state.db,
    # not the dashboard's launch profile. Stored on the session so _start_agent_build
    # and each turn re-bind HERMES_HOME. None/own profile → launch (unchanged).
    profile = (params.get("profile") or "").strip() or None
    profile_home = _profile_home(profile)

    # The desktop composer owns its model/effort/fast as plain UI state and ships
    # it on every session.create. Honor each as a PER-SESSION override (built into
    # the agent below) — never a global config write, so picking a model/effort
    # for a new chat can't mutate the profile default. provider is optional
    # (resolved at build).
    create_model = str(params.get("model") or "").strip()
    session_model_override = (
        {"model": create_model, "provider": str(params.get("provider") or "").strip() or None}
        if create_model
        else None
    )
    create_reasoning_override = None
    if effort := str(params.get("reasoning_effort") or "").strip():
        try:
            from hermes_constants import parse_reasoning_effort

            create_reasoning_override = parse_reasoning_effort(effort)
        except Exception:
            create_reasoning_override = None
    # Presence is part of the contract: omitted means inherit the profile,
    # true pins priority, and false pins normal. Empty string is the internal
    # explicit-normal sentinel because _make_agent uses None for inheritance.
    create_service_tier_override = None
    if "fast" in params:
        create_service_tier_override = (
            "priority" if is_truthy_value(params.get("fast")) else ""
        )

    ready = threading.Event()
    now = time.time()
    lease = None  # claimed lazily on the first turn (_ensure_active_session_slot)

    with _sessions_lock:
        _sessions[sid] = {
            "agent": None,
            "agent_error": None,
            "agent_ready": ready,
            "attached_images": [],
            "close_on_disconnect": is_truthy_value(params.get("close_on_disconnect", False)),
            "active_session_lease": lease,
            "cols": cols,
            "created_at": now,
            "edit_snapshots": {},
            "explicit_cwd": explicit_cwd,
            "history": history,
            "history_lock": threading.Lock(),
            "history_version": 0,
            "image_counter": 0,
            "cwd": resolved_cwd,
            "inflight_turn": None,
            "last_active": now,
            "model_override": session_model_override,
            "create_reasoning_override": create_reasoning_override,
            "create_service_tier_override": create_service_tier_override,
            "parent_session_id": parent_session_id,
            "pending_title": title or None,
            "profile_home": str(profile_home) if profile_home is not None else None,
            "running": False,
            "session_key": key,
            "show_reasoning": _load_show_reasoning(),
            "source": source,
            "slash_worker": None,
            "tool_progress_mode": _load_tool_progress_mode(),
            "tool_started_at": {},
            "transport": current_transport() or _stdio_transport,
            "cxba_binding": cxba_binding,
        }
        _register_session_cwd(_sessions[sid])

    # Ordinary Hermes drafts remain lazy so opening an unused composer does not
    # leave an empty row.  A trusted CXBA create is different: Spring has already
    # created a durable business branch and receives ``stored_session_id`` as its
    # stable continuation handle.  Persist that empty row before acknowledging
    # the create so close/reconnect can resume it even before the first prompt.
    if cxba_binding is not None:
        try:
            _ensure_session_db_row(_sessions[sid])
            with _profile_db(params) as cxba_db:
                persisted = cxba_db.get_session(key) if cxba_db is not None else None
            if persisted is None:
                raise RuntimeError("trusted CXBA session row was not persisted")
        except Exception:
            logger.exception(
                "event=cxba_session stage=create status=persist_failed sessionId=%s",
                key,
            )
            with _sessions_lock:
                failed_session = _sessions.pop(sid, None)
            _teardown_popped_session(failed_session, end_reason="create_persist_failed")
            return _err(rid, 5008, "trusted CXBA session could not be persisted")

    # Return the lightweight session immediately so Ink can paint the composer
    # + skeleton panel, then build the real AIAgent just after this response is
    # flushed.  This keeps startup responsive while still hydrating tools/skills
    # without requiring the user to submit a first prompt.
    _schedule_agent_build(sid)
    _schedule_session_cap_enforcement()  # trim detached idle sessions over the cap

    return _ok(
        rid,
        {
            "session_id": sid,
            "stored_session_id": key,
            "message_count": len(history),
            "messages": _history_to_messages(
                history, include_raw_indexes=cxba_binding is not None
            ),
            "info": {
                # Reflect the per-session model override (desktop composer pick)
                # in the immediate response so the client doesn't briefly clobber
                # its sticky pick with the global default before the deferred
                # build's session.info lands.
                "model": (
                    session_model_override.get("model")
                    if session_model_override
                    else _resolve_model()
                ),
                **(
                    {"provider": session_model_override["provider"]}
                    if session_model_override and session_model_override.get("provider")
                    else {}
                ),
                "tools": {},
                "skills": {},
                "cwd": _sessions[sid]["cwd"],
                "branch": _git_branch_for_cwd(_sessions[sid]["cwd"]),
                "project": _project_info_for_cwd(_sessions[sid]["cwd"]),
                "lazy": True,
                "desktop_contract": DESKTOP_BACKEND_CONTRACT,
                "profile_name": _response_profile_name(profile),
            },
        },
    )


@method("session.list")
def _(rid, params: dict) -> dict:
    from tui_gateway.cxba_runtime import binding_from_model_config
    from tui_gateway.transport import has_cxba_private_authority

    with _profile_db(params) as db:
        if db is None:
            return _db_unavailable_error(rid, code=5006)
        try:
            # Resume picker should surface human conversation sessions from every
            # user-facing surface — CLI, TUI, all gateway platforms (including new
            # ones not enumerated here), ACP adapter clients, webhook sessions,
            # custom `HERMES_SESSION_SOURCE` values, and older installs with
            # different source labels. We deny-list only the noisy internal
            # sources (``tool`` sub-agent runs and ``kanban`` dispatcher
            # workers) rather than allow-listing a fixed set of platform names
            # that goes stale whenever a new platform is added or a user names
            # their own source.
            deny = frozenset({"kanban", "tool"})

            limit = int(params.get("limit", 200) or 200)
            # Over-fetch modestly so per-source filtering doesn't leave us
            # short; the compression-tip projection in ``list_sessions_rich``
            # can also merge rows.
            fetch_limit = max(limit * 2, 200)
            rows = [
                s
                for s in db.list_sessions_rich(
                    source=None,
                    limit=fetch_limit,
                    order_by_last_active=True,
                    compact_rows=True,
                )
                if (s.get("source") or "").strip().lower() not in deny
                and (
                    has_cxba_private_authority()
                    or binding_from_model_config(s.get("model_config")) is None
                )
            ][:limit]
            return _ok(
                rid,
                {
                    "sessions": [
                        {
                            "id": s["id"],
                            "title": s.get("title") or "",
                            "preview": s.get("preview") or "",
                            "started_at": s.get("started_at") or 0,
                            "message_count": s.get("message_count") or 0,
                            "source": s.get("source") or "",
                        }
                        for s in rows
                    ]
                },
            )
        except Exception as e:
            return _err(rid, 5006, str(e))


@method("session.most_recent")
def _(rid, params: dict) -> dict:
    """Return the most recent human-facing session id, or ``None``.

    Mirrors ``session.list``'s deny-list behaviour (drops ``tool``
    sub-agent rows and ``kanban`` worker rows).  Used by TUI auto-resume when
    ``display.tui_auto_resume_recent`` is on; the field is also handy
    for any CLI tooling that wants "latest session" without paginating
    the full list.

    Contract: a ``{"session_id": null}`` result means "no eligible
    session found right now".  Errors are also folded into that
    null-result shape (and logged) so callers don't have to special-
    case JSON-RPC error envelopes for what is a normal "no answer".

    Honors ``params.profile`` so app-global remote mode lists from the
    focused profile's ``state.db`` (mirrors ``session.resume``).
    """
    with _profile_db(params) as db:
        if db is None:
            return _ok(rid, {"session_id": None})
        try:
            deny = frozenset({"kanban", "tool"})
            # Over-fetch by a generous bounded amount so heavy sub-agent
            # users (lots of recent ``tool`` rows) don't get a false
            # "no eligible session" answer.  ``session.list`` uses a
            # similar over-fetch strategy.
            rows = db.list_sessions_rich(
                source=None, limit=200, order_by_last_active=True, compact_rows=True
            )
            from tui_gateway.cxba_runtime import binding_from_model_config
            from tui_gateway.transport import has_cxba_private_authority

            for row in rows:
                src = (row.get("source") or "").strip().lower()
                if src in deny:
                    continue
                if (
                    not has_cxba_private_authority()
                    and binding_from_model_config(row.get("model_config")) is not None
                ):
                    continue
                return _ok(
                    rid,
                    {
                        "session_id": row.get("id"),
                        "title": row.get("title") or "",
                        "started_at": row.get("started_at") or 0,
                        "source": row.get("source") or "",
                    },
                )
            return _ok(rid, {"session_id": None})
        except Exception:
            logger.exception("session.most_recent failed")
            return _ok(rid, {"session_id": None})


@method("project.facts")
def _(rid, params: dict) -> dict:
    """Structured project facts for a cwd — manifests, package manager, the
    exact verify commands, and context files.

    The same detection the coding-context posture (#43316) bakes into the system
    prompt, exposed so UIs (the desktop verify surface) consume it instead of
    re-sniffing. ``{"facts": null}`` means the cwd isn't a code workspace.
    """
    try:
        from agent.coding_context import project_facts_for

        return _ok(rid, {"facts": project_facts_for(params.get("cwd"))})
    except Exception:
        logger.exception("project.facts failed")
        return _ok(rid, {"facts": None})


@method("verification.status")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Best known coding verification evidence for a cwd/session.

    Read-only consumer of the core ledger. It never runs checks and never
    upgrades targeted evidence into a repository-wide guarantee.
    """
    try:
        from agent.verification_evidence import verification_status

        return _ok(
            rid,
            {
                "verification": verification_status(
                    session_id=params.get("session_id") or params.get("session_key"),
                    cwd=params.get("cwd"),
                )
            },
        )
    except Exception:
        logger.exception("verification.status failed")
        return _ok(rid, {"verification": {"status": "unknown", "evidence": None}})


@method("session.resume")
def _(rid, params: dict) -> dict:
    target = params.get("session_id", "")
    if not target:
        return _err(rid, 4006, "session_id required")
    try:
        cols = int(params.get("cols", 80))
    except (TypeError, ValueError):
        cols = 80
    # ``profile`` (app-global remote mode): resume a session that lives in another
    # local profile's state.db. None/own profile → the launch profile (unchanged).
    profile = (params.get("profile") or "").strip() or None
    profile_home = _profile_home(profile)
    # Desktop hydrates persisted transcripts through the authenticated REST
    # route in parallel. Suppress the duplicate WebSocket transcript only when
    # the caller explicitly requests it; other clients keep upstream behavior.
    omit_messages = is_truthy_value(params.get("omit_messages", False))

    # In a profile scope this opens a DEDICATED handle we own until the agent
    # takes it (see the ownership transfer at _init_session below); every path
    # that returns before that transfer must close it. Otherwise reuse the
    # shared launch db, which outlives the RPC and is never closed here.
    owns_db = False
    if profile_home is not None:
        from hermes_state import SessionDB

        db = SessionDB(db_path=profile_home / "state.db")
        owns_db = True
    else:
        db = _get_db()
    try:
        if db is None:
            return _db_unavailable_error(rid, code=5000)

        found = db.get_session(target)
        if not found:
            found = db.get_session_by_title(target)
            if found:
                target = found["id"]
            elif is_truthy_value(params.get("lazy", False)) and _child_run_active(target):
                # Race: a watch window opened on a freshly-spawned subagent. The
                # child relays `subagent.start` (which carries child_session_id and
                # triggers the window) BEFORE its first run_conversation() flushes
                # the DB row via _ensure_db_session, so db.get_session(target) is
                # momentarily empty. On slower hosts (notably WSL2, where SQLite +
                # process scheduling widen the gap) the window's resume consistently
                # lands inside this window and used to hard-fail "session not found"
                # — the frontend then 404'd on the REST messages fallback and the
                # window spun forever. The child is provably live (_child_run_active),
                # so proceed into the lazy branch with empty history; the live mirror
                # streams the whole turn anyway and the row exists by upgrade time.
                found = {}
            else:
                return _err(rid, 4007, "session not found")

        from tui_gateway.cxba_runtime import binding_from_model_config, validate_session_binding
        from tui_gateway.transport import has_cxba_private_authority

        stored_cxba_binding = binding_from_model_config(found.get("model_config"))
        suppress_auto_continue = is_truthy_value(
            params.get("suppress_auto_continue", False)
        )
        if suppress_auto_continue and (
            stored_cxba_binding is None or not has_cxba_private_authority()
        ):
            return _err(
                rid,
                4033,
                "suppress_auto_continue requires a trusted CXBA private connection",
            )
        raw_model_config = found.get("model_config")
        if isinstance(raw_model_config, str):
            try:
                raw_model_config = json.loads(raw_model_config)
            except (TypeError, ValueError):
                raw_model_config = {}
        if (
            stored_cxba_binding is not None
            and isinstance(raw_model_config, dict)
            and raw_model_config.get("_cxba_retired") is True
        ):
            return _err(rid, 4100, "CXBA business Session is retired and cannot resume")
        supplied_cxba_binding = None
        if "cxba_context" in params:
            if not has_cxba_private_authority():
                return _err(
                    rid, 4033, "cxba_context requires a trusted CXBA private connection"
                )
            try:
                supplied_cxba_binding = validate_session_binding(params.get("cxba_context"))
            except ValueError as exc:
                return _err(rid, 4034, f"invalid trusted cxba_context: {exc}")
        if stored_cxba_binding is not None:
            if not has_cxba_private_authority():
                return _err(rid, 4035, "CXBA session resume requires a trusted private connection")
            if supplied_cxba_binding is not None and supplied_cxba_binding != stored_cxba_binding:
                return _err(rid, 4036, "cxba_context does not match the stored session binding")
        elif supplied_cxba_binding is not None:
            return _err(
                rid, 4036, "an existing Hermes session cannot acquire a CXBA binding on resume"
            )

        # Follow the compression-continuation chain to the live tip so a resume on
        # a rotated-out parent id binds to the descendant that actually holds the
        # post-compression turns. Auto-compression ends the session and forks a
        # continuation child; without this, resuming the original id (the desktop's
        # routed id when the chat was opened before it rotated) reloads the parent
        # transcript and the response generated after compression is missing — the
        # "I came back and the reply isn't there" bug on large sessions. Resolving
        # here also re-anchors the fast path below so a still-live rotated session
        # is reused (by its new key) instead of rebuilding a duplicate agent on the
        # stale parent. Skipped for lazy watch windows, which intentionally attach
        # to the exact child branch they were opened on.
        if found and not is_truthy_value(params.get("lazy", False)):
            try:
                tip = db.resolve_resume_session_id(target)
            except Exception:
                tip = target
            if tip and tip != target:
                target = tip
                found = db.get_session(target) or found

        profile_resume_cwd = str(found.get("cwd") or "").strip() or _profile_configured_cwd(
            profile_home
        )

        def _reuse_live_payload(sid: str, session: dict) -> dict:
            payload = _live_session_payload(
                sid,
                session,
                cols=cols,
                touch=True,
                transport=current_transport() or _stdio_transport,
                omit_messages=omit_messages,
            )
            payload["resumed"] = target
            # A lazy watch session never owns a run loop, so its payload's running
            # flag is always False — overlay the child-run registry so a reconnecting
            # watch window keeps its busy indicator while the child is still mid-run.
            if session.get("agent") is None and _child_run_active(target):
                payload["running"] = True
                payload["status"] = "streaming"
            return payload

        # Fast path: if the session is already live, reuse it. A trusted caller
        # can require an eager agent because approval continuation cannot accept
        # an RPC and then discover that a deferred live record has no agent.
        live = None
        with _session_resume_lock:
            if (
                stored_cxba_binding is not None
                and db.get_session_model_config_value(
                    target, "_cxba_retired", False
                )
                is True
            ):
                return _err(
                    rid, 4100, "CXBA business Session is retired and cannot resume"
                )
            live = _find_live_session_by_key(target)
        if live is not None:
            live_sid, live_session = live
            if (
                is_truthy_value(params.get("eager_build", False))
                and live_session.get("agent") is None
            ):
                _start_agent_build(live_sid, live_session)
                if build_error := _wait_agent(live_session, rid):
                    return build_error
            return _ok(rid, _reuse_live_payload(live_sid, live_session))

        # Lazy/watch resume: register the live session WITHOUT building an agent.
        # Used by the desktop's subagent windows — the child runs inside the
        # parent's turn, so its window only needs the stored history plus a
        # transport for the child-mirror's live events. Skipping _make_agent here
        # is what keeps the window cheap while the backend is busy running the
        # delegation. A later prompt.submit upgrades it via _start_agent_build
        # (resume_session_id keeps the upgrade on the stored conversation).
        if is_truthy_value(params.get("lazy", False)):
            sid = uuid.uuid4().hex[:8]
            source = _resolve_session_source(
                str(params.get("source") or found.get("source") or "").strip() or None
            )
            lease = None  # claimed lazily on the first turn (_ensure_active_session_slot)
            try:
                db.reopen_session(target)
                # The child's OWN conversation only — include_ancestors would prepend
                # the parent's transcript onto the subagent's branch.
                # repair_alternation: this resume feeds LIVE REPLAY (the loaded
                # history becomes the resumed session record's working conversation),
                # so heal a durable ``user;user`` violation once here instead of
                # re-firing the pre-request repair on every subsequent turn.
                history = db.get_messages_as_conversation(target, repair_alternation=True)
            except Exception as e:
                if lease is not None:
                    lease.release()
                return _err(rid, 5000, f"resume failed: {e}")
            cwd = profile_resume_cwd or _default_session_cwd()
            record = _deferred_session_record(
                target,
                cols=cols,
                cwd=cwd,
                history=history,
                lease=lease,
                source=source,
                close_on_disconnect=is_truthy_value(params.get("close_on_disconnect", False)),
                profile_home=profile_home,
                lazy=True,
            )
            record["cxba_binding"] = stored_cxba_binding
            if (live := _claim_or_reuse_live(sid, target, record, lease)) is not None:
                return _ok(rid, _reuse_live_payload(*live))
            # A delegated child mid-run emits no session events of its own — report
            # its liveness from the relay registry so the window shows a busy turn.
            child_running = _child_run_active(target)
            # User-visible messages use the VERBATIM display projection (child-only,
            # no ancestors — matching the repaired read above), so model-invisible
            # rows persisted by #65919 (verification candidates collapsed by
            # repair_message_sequence) survive in the watch window just as they do
            # on the eager resume + REST paths. The repaired ``history`` above still
            # feeds live replay. Fall back to it if the display read fails.
            try:
                display_history = db.get_messages_as_conversation(
                    target, repair_alternation=False, include_row_ids=True
                )
            except Exception:
                logger.debug("child-watch display projection read failed", exc_info=True)
                display_history = history
            messages = [] if omit_messages else _history_to_messages(
                display_history, include_raw_indexes=stored_cxba_binding is not None
            )
            return _ok(
                rid,
                {
                    "session_id": sid,
                    "resumed": target,
                    "message_count": len(display_history) if omit_messages else len(messages),
                    "messages": messages,
                    "messages_omitted": omit_messages,
                    "info": _lazy_resume_info(cwd, profile=profile),
                    "inflight": None,
                    "running": child_running,
                    "session_key": target,
                    "started_at": record["created_at"],
                    "status": "streaming" if child_running else "idle",
                },
            )

        # Cold resume default: register the live session and read its stored
        # transcript, but build the agent OFF the response path. _make_agent can
        # block for seconds (MCP discovery, prompt/skill build, AIAgent
        # construction), and every resume caller (desktop + Ink TUI) awaits this RPC
        # before it paints — so building eagerly is the bulk of the multi-second
        # "switching sessions is frozen" latency. Return the full display transcript
        # immediately and pre-warm the agent on a short timer (the same deferred-
        # build contract session.create uses); _sess() also builds on demand if the
        # first prompt beats the timer. A caller that needs the agent built
        # synchronously (e.g. tests of the build race) passes ``eager_build: true``
        # to fall through to the eager path below. Distinct from the lazy/watch
        # branch above: a normal resume restores the full ancestor history and the
        # session's persisted runtime identity, and is a real (upgradable) session.
        if not is_truthy_value(params.get("eager_build", False)):
            sid = uuid.uuid4().hex[:8]
            source = _resolve_session_source(
                str(params.get("source") or found.get("source") or "").strip() or None
            )
            lease = None  # claimed lazily on the first turn (_ensure_active_session_slot)
            # Interactive resume routes approvals/clarify through gateway prompts;
            # the deferred build wires the remaining per-session callbacks.
            _enable_gateway_prompts()
            try:
                db.reopen_session(target)
                # One lineage SELECT feeds both projections (#67142-adjacent perf,
                # from the desktop audit): the model-fed copy is alternation-repaired
                # (raw_history → sanitize_replay_history → the resumed session's
                # working conversation) and the display copy stays verbatim —
                # inspection/export must show what is actually stored.
                if omit_messages:
                    raw_history = db.get_messages_as_conversation(
                        target, repair_alternation=True
                    )
                    display_history = []
                else:
                    raw_history, display_history = db.get_resume_conversations(target)
            except Exception as e:
                if lease is not None:
                    lease.release()
                return _err(rid, 5000, f"resume failed: {e}")
            # Display keeps the full transcript; the model-fed history drops a
            # dangling/interrupted tool-call tail so a session killed mid-loop does
            # not replay the unanswered call forever (#29086).
            prefix = [] if omit_messages else db.get_ancestor_display_prefix(target)
            history = sanitize_replay_history(raw_history)
            # Ordinary chats restore their stored runtime; trusted CXBA chats
            # deliberately build from the current deployment route. Both the
            # deferred and eager paths use the same selector.
            overrides = _resume_runtime_overrides(found, stored_cxba_binding) or {}
            model_override = overrides.get("model_override") or {}
            cwd = profile_resume_cwd or _default_session_cwd()
            record = _deferred_session_record(
                target,
                cols=cols,
                cwd=cwd,
                history=history,
                lease=lease,
                source=source,
                close_on_disconnect=is_truthy_value(params.get("close_on_disconnect", False)),
                display_history_prefix=prefix,
                profile_home=profile_home,
                model_override=overrides.get("model_override"),
                resume_runtime_overrides=overrides or None,
            )
            record["cxba_binding"] = stored_cxba_binding
            if (live := _claim_or_reuse_live(sid, target, record, lease)) is not None:
                return _ok(rid, _reuse_live_payload(*live))

            _schedule_agent_build(sid)
            _schedule_session_cap_enforcement()  # trim detached idle sessions over the cap
            auto_continue = (
                None
                if suppress_auto_continue
                else _maybe_schedule_auto_continue(sid, record, target)
            )

            messages = [] if omit_messages else _history_to_messages(
                display_history, include_raw_indexes=stored_cxba_binding is not None
            )
            payload = {
                "session_id": sid,
                "resumed": target,
                "message_count": len(raw_history) if omit_messages else len(messages),
                "messages": messages,
                "messages_omitted": omit_messages,
                "info": _lazy_resume_info(
                    cwd,
                    model=model_override.get("model") or "",
                    provider=overrides.get("provider_override") or "",
                    profile=profile,
                ),
                "inflight": None,
                "running": False,
                "session_key": target,
                "started_at": record["created_at"],
                "status": "idle",
            }
            if auto_continue is not None:
                payload["auto_continue"] = auto_continue
            return _ok(rid, payload)

        # Build the agent OUTSIDE the lock — _make_agent can block for seconds
        # (MCP discovery, prompt/skill build, AIAgent construction). Holding
        # _session_resume_lock across it would stall session.close on the main
        # dispatch thread (it's not a _LONG_HANDLER), blocking fast-path RPCs.
        sid = uuid.uuid4().hex[:8]
        source = _resolve_session_source(
            str(params.get("source") or found.get("source") or "").strip() or None
        )
        lease = None  # claimed lazily on the first turn (_ensure_active_session_slot)
        _enable_gateway_prompts()
        home_token = (
            set_hermes_home_override(str(profile_home)) if profile_home is not None else None
        )
        secret_token = (
            set_secret_scope(build_profile_secret_scope(Path(str(profile_home))))
            if profile_home is not None
            else None
        )
        try:
            db.reopen_session(target)
            # One lineage SELECT feeds both projections (see the interactive resume
            # above): the model-fed copy is alternation-repaired for LIVE REPLAY, the
            # display copy stays verbatim.
            if omit_messages:
                raw_history = db.get_messages_as_conversation(
                    target, repair_alternation=True
                )
                display_history = []
            else:
                raw_history, display_history = db.get_resume_conversations(target)
            # The display transcript keeps every row so the user still sees their
            # full history.  The model-fed history is sanitized: a session whose
            # last turn died mid-tool-loop persists a dangling assistant(tool_calls)
            # (or interrupted assistant→tool) tail; replaying it makes the model
            # re-issue the unanswered call forever — the permanent-"thinking" stuck
            # session in #29086.  The messaging gateway already strips this; this is
            # the WebUI/TUI resume path picking up the same cleanup.
            display_history_prefix = (
                [] if omit_messages else db.get_ancestor_display_prefix(target)
            )
            history = sanitize_replay_history(raw_history)
            messages = [] if omit_messages else _history_to_messages(
                display_history, include_raw_indexes=stored_cxba_binding is not None
            )
            tokens = _set_session_context(target)
            try:
                # Pass the profile's db so the agent persists turns to the right
                # state.db; home override is active here so config/skills/model
                # resolve to the profile too. Ordinary chats restore their stored
                # runtime identity; trusted CXBA chats follow the current deployment.
                stored_runtime_overrides = _resume_runtime_overrides(
                    found, stored_cxba_binding
                )
                agent = _make_agent(
                    sid,
                    target,
                    session_id=target,
                    session_db=db,
                    platform_override=source,
                    **stored_runtime_overrides,
                )
            finally:
                _clear_session_context(tokens)
        except Exception as e:
            if lease is not None:
                lease.release()
            return _err(rid, 5000, f"resume failed: {e}")
        finally:
            if home_token is not None:
                reset_hermes_home_override(home_token)
            if secret_token is not None:
                reset_secret_scope(secret_token)

        # Double-checked locking: another concurrent resume may have created the
        # live session while we were building. Re-check under the lock; if it won,
        # discard our just-built agent and reuse theirs (no worker/poller wired yet).
        with _session_resume_lock:
            if (
                stored_cxba_binding is not None
                and db.get_session_model_config_value(
                    target, "_cxba_retired", False
                )
                is True
            ):
                try:
                    if hasattr(agent, "close"):
                        agent.close()
                finally:
                    if lease is not None:
                        lease.release()
                return _err(
                    rid, 4100, "CXBA business Session is retired and cannot resume"
                )
            live = _find_live_session_by_key(target)
            if live is not None:
                try:
                    if hasattr(agent, "close"):
                        agent.close()
                except Exception as exc:
                    if session.get("cxba_binding") is not None:
                        return _err(
                            rid,
                            5008,
                            f"trusted session history could not be loaded: {exc}",
                        )
                    logger.debug("session.history database lookup failed", exc_info=True)
                if lease is not None:
                    lease.release()
                other_sid, other_session = live
                payload = _live_session_payload(
                    other_sid,
                    other_session,
                    cols=cols,
                    touch=True,
                    transport=current_transport() or _stdio_transport,
                    omit_messages=omit_messages,
                )
                payload["resumed"] = target
                return _ok(rid, payload)
            try:
                init_home_token = (
                    set_hermes_home_override(str(profile_home))
                    if profile_home is not None
                    else None
                )
                init_secret_token = (
                    set_secret_scope(build_profile_secret_scope(Path(str(profile_home))))
                    if profile_home is not None
                    else None
                )
                try:
                    _init_session(
                        sid,
                        target,
                        agent,
                        history,
                        cols=cols,
                        cwd=profile_resume_cwd,
                        session_db=db,
                        source=source,
                    )
                    # Ownership TRANSFER — the registered session's agent now
                    # holds this handle for its whole life, and _init_session
                    # never closes a caller-supplied session_db (its
                    # _init_owns_db stays False). Closing it in the finally
                    # below would fault every later turn on this session with
                    # "Cannot operate on a closed database".
                    #
                    # Ownership moves ONTO the agent rather than just being
                    # dropped: AIAgent.close() (reached from _teardown_session
                    # on session.close and the orphaned-session reaper) closes
                    # a handle it owns, so the dedicated fds and the token
                    # writer are released at teardown instead of living as
                    # long as the gateway process.
                    #
                    # The drop is UNCONDITIONAL and the transfer is best-effort
                    # on top of it, deliberately. Past this line the session is
                    # registered and holding this handle, so the finally must
                    # not close it even if the transfer was refused — a refusal
                    # leaves the old leak, which is survivable; closing under a
                    # live session is the permanent "Cannot operate on a closed
                    # database" break this patch exists to avoid.
                    _transfer_db_to_agent(agent, db)
                    owns_db = False
                finally:
                    if init_home_token is not None:
                        reset_hermes_home_override(init_home_token)
                    if init_secret_token is not None:
                        reset_secret_scope(init_secret_token)
                if sid in _sessions:
                    _sessions[sid]["cxba_binding"] = stored_cxba_binding
                    if stored_runtime_overrides.get("model_override") is not None:
                        _sessions[sid]["model_override"] = stored_runtime_overrides[
                            "model_override"
                        ]
                    _sessions[sid]["display_history_prefix"] = display_history_prefix
                    # Remember the profile home so each turn re-binds HERMES_HOME (the
                    # agent persists to its own db, but mid-turn home reads — memory,
                    # skills — must resolve to the resumed profile too).
                    if profile_home is not None:
                        _sessions[sid]["profile_home"] = str(profile_home)
                    _sessions[sid]["active_session_lease"] = lease
            except Exception as e:
                # _init_session registers _sessions[sid] BEFORE its first read
                # through this handle. If it raised in between — "database is
                # locked" is the realistic trigger — the half-built session is
                # still registered while the finally below closes the handle it
                # holds, and the live-session fast path above would then serve
                # that dead session on every later resume of this id
                # ("'NoneType' object has no attribute 'execute'", permanently).
                # owns_db still True means ownership never transferred, so the
                # registration is ours to undo.
                if owns_db:
                    with _sessions_lock:
                        _sessions.pop(sid, None)
                if lease is not None:
                    lease.release()
                return _err(rid, 5000, f"resume failed: {e}")
            session = _sessions.get(sid) or {}
    finally:
        # Every return that does NOT reach the transfer above abandons this
        # handle — session-not-found, both "resume failed" paths, the live-session
        # fast path (the hot one: reconnects re-resume live chats through it), the
        # deferred cold/lazy returns, and the double-checked-locking discard.
        # Dropping it merely relied on refcounting to release the sqlite fds; that
        # stops being true the moment anything pins the instance — SessionDB pins
        # ITSELF once its background token writer starts, via
        # atexit.register(_drain_token_queue_at_exit) (hermes_state.py), which only
        # close() unregisters. A pinned handle keeps its db/-wal/-shm fds and its
        # writer thread for the life of the process.
        if owns_db and db is not None:
            with contextlib.suppress(Exception):
                db.close()
    auto_continue = (
        _maybe_schedule_auto_continue(sid, session, target)
        if session and not suppress_auto_continue
        else None
    )
    payload = {
        "session_id": sid,
        "resumed": target,
        "message_count": len(raw_history) if omit_messages else len(messages),
        "messages": messages,
        "messages_omitted": omit_messages,
        "info": _session_info(agent, session),
        "inflight": None,
        "running": False,
        "session_key": target,
        "started_at": float(session.get("created_at") or time.time()),
        "status": "idle",
    }
    if auto_continue is not None:
        payload["auto_continue"] = auto_continue
    return _ok(rid, payload)


@method("session.cwd.set")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    if session.get("running"):
        return _err(rid, 4009, "session busy")
    raw = str(params.get("cwd", "") or "").strip()
    if not raw:
        return _err(rid, 4016, "cwd required")
    try:
        cwd = _set_session_cwd(session, raw)
    except ValueError as e:
        return _err(rid, 4017, str(e))
    agent = session.get("agent")
    info = _session_info(agent, session) if agent is not None else {
        "cwd": cwd,
        "branch": _git_branch_for_cwd(cwd),
        "project": _project_info_for_cwd(cwd),
        "lazy": True,
    }
    _emit("session.info", params.get("session_id", ""), info)
    return _ok(rid, info)


@method("session.workspace.move")
def _(rid, params: dict) -> dict:
    """Re-home a STORED session's workspace into another folder/project.

    Unlike ``session.cwd.set`` (which acts on a live runtime session by its UI
    id), this targets a persisted row by ``session_key`` so the desktop can fix
    a session that was created in the wrong directory — no live agent required.
    The git branch/root columns are REPLACED (not merely enriched), because the
    whole point of the move is to change which project claims the session; a
    stale ``git_repo_root`` would keep it grouped under the project it left.

    A live agent bound to the row follows through the runtime path too, so its
    terminal/file tools re-anchor immediately; a mid-turn session refuses the
    move rather than yanking the workspace out from under its tools.
    """
    target = str(params.get("session_key") or "").strip()
    if not target:
        return _err(rid, 4007, "session_key required")
    raw = str(params.get("cwd", "") or "").strip()
    if not raw:
        return _err(rid, 4016, "cwd required")
    from hermes_constants import translate_cwd_for_wsl_backend

    resolved = os.path.abspath(os.path.expanduser(translate_cwd_for_wsl_backend(raw)))
    if not os.path.isdir(resolved):
        return _err(rid, 4017, f"working directory does not exist: {raw}")

    # Snapshot under the lock — concurrent RPCs mutate _sessions (same pattern
    # as _cwd_for_session_key).
    live = None
    live_sid = ""
    with _sessions_lock:
        for sid, sess in list(_sessions.items()):
            if sess.get("session_key") == target:
                live, live_sid = sess, sid
                break
    if live is not None and live.get("running"):
        return _err(rid, 4009, "session busy")

    branch = _git_branch_for_cwd(resolved)
    root = _git_common_repo_root_for_cwd(resolved)
    with _profile_db(params) as db:
        if db is None:
            return _db_unavailable_error(rid, code=5007)
        # A brand-new draft has no persisted row yet; the live re-home below
        # still applies and the row inherits the cwd when it is first written.
        row_exists = bool(db.get_session(target))
        if not row_exists and live is None:
            return _err(rid, 4007, "session not found")
        if row_exists:
            try:
                db.update_session_cwd(
                    target, resolved, branch, root, replace_git_meta=True
                )
            except Exception as e:
                return _err(rid, 5007, f"move failed: {e}")

    if live is not None:
        try:
            _set_session_cwd(live, resolved)
        except ValueError as e:
            return _err(rid, 4017, str(e))
        agent = live.get("agent")
        info = _session_info(agent, live) if agent is not None else {
            "cwd": resolved,
            "branch": branch,
            "project": _project_info_for_cwd(resolved),
            "lazy": True,
        }
        _emit("session.info", live_sid, info)

    return _ok(rid, {"cwd": resolved, "branch": branch, "git_repo_root": root})


@method("session.active_list")
def _(rid, params: dict) -> dict:
    """Return live TUI sessions in this gateway process.

    Unlike ``session.list`` this is not a historical DB browser: it reports only
    sessions with in-memory agents/workers that the current TUI can switch to
    without closing siblings.
    """
    current = str(params.get("current_session_id") or "")
    try:
        with _sessions_lock:
            snapshot = list(_sessions.items())
    except Exception as e:
        return _err(rid, 5036, f"could not enumerate active sessions: {e}")

    # Liveness filter (#38950): a session whose teardown has begun (``_finalized``)
    # is dead — its agent/worker are being released and it is no longer
    # attachable — but it can briefly remain in ``_sessions`` until the reaper
    # pops it (the WS grace-reap and idle reaper both set ``_finalized`` inside
    # ``_teardown_session`` before the pop). Counting these inflated the footer's
    # "N sessions" count, which only ever went up until a gateway restart. Drop
    # them here so the count reflects genuinely attachable sessions. We do NOT
    # filter on ``transport is _detached_ws_transport`` (the WS-detached drop
    # sentinel): a detached session is still attachable via a quick reconnect /
    # session.resume until the grace-reap finalizes it, and a standalone
    # ``hermes --tui`` session legitimately rides the real stdio transport and
    # must stay visible.
    # Keep the natural creation/insertion order from ``_sessions``.  The
    # frontend marks the focused session with ``current``; it should not jump to
    # the top just because the user switched to it.
    from tui_gateway.transport import has_cxba_private_authority

    rows = [
        _session_live_item(sid, session, current)
        for sid, session in snapshot
        if not session.get("_finalized")
        and (
            session.get("cxba_binding") is None
            or has_cxba_private_authority()
        )
    ]
    return _ok(rid, {"sessions": rows})


@method("session.activate")
def _(rid, params: dict) -> dict:
    """Attach the frontend to an already-live TUI session.

    This intentionally does not close the previously focused session; it merely
    returns enough state for Ink to redraw around another live session id.
    """
    sid = str(params.get("session_id") or "")
    session, err = _sess_nowait({"session_id": sid}, rid)
    if err:
        return err
    assert session is not None

    return _ok(
        rid,
        _live_session_payload(
            sid,
            session,
            touch=True,
            transport=current_transport() or _stdio_transport,
            omit_messages=is_truthy_value(params.get("omit_messages", False)),
        ),
    )


@method("session.delete")
def _(rid, params: dict) -> dict:
    """Delete a stored session and its on-disk transcript files.

    Used by the TUI resume picker (``d`` key) so users can prune old
    sessions without dropping to the CLI.  Refuses to delete a session
    that is currently active in this gateway process — those rows are
    still being written to and removing them out from under the live
    agent corrupts message ordering and trips FK constraints when the
    next message append flushes.

    Honors ``params.profile`` so app-global remote mode deletes from the
    focused profile's ``state.db`` + sessions dir (mirrors ``session.resume``).
    """
    target = params.get("session_id", "")
    if not target:
        return _err(rid, 4006, "session_id required")
    # Block deletion of any session currently bound to a live TUI session
    # in this process.  The picker hides the active session anyway, but a
    # racing caller could still target it.  Snapshot via ``list(...)``
    # because ``_sessions`` is mutated by concurrent RPCs on the thread
    # pool — iterating the dict directly can raise ``RuntimeError:
    # dictionary changed size during iteration``.  If even the snapshot
    # raises, fail closed (refuse the delete) rather than fail open.
    try:
        with _sessions_lock:
            snapshot = list(_sessions.values())
    except Exception as e:
        return _err(rid, 5036, f"could not enumerate active sessions: {e}")
    active = {s.get("session_key") for s in snapshot if s.get("session_key")}
    if target in active:
        return _err(rid, 4023, "cannot delete an active session")
    profile = (params.get("profile") or "").strip() or None
    profile_home = _profile_home(profile)
    with _profile_db(params) as db:
        if db is None:
            return _db_unavailable_error(rid, code=5036)
        stored = db.get_session(target) if hasattr(db, "get_session") else None
        if stored is not None:
            from tui_gateway.cxba_runtime import binding_from_model_config

            if binding_from_model_config(stored.get("model_config")) is not None:
                return _err(
                    rid,
                    4035,
                    "CXBA Session history cannot be physically deleted; retire its runtime state",
                )
        if profile_home is not None:
            sessions_dir = Path(profile_home) / "sessions"
        else:
            sessions_dir = get_hermes_home() / "sessions"
        try:
            deleted = db.delete_session(target, sessions_dir=sessions_dir)
        except Exception as e:
            return _err(rid, 5036, f"delete failed: {e}")
        if not deleted:
            return _err(rid, 4007, "session not found")
        return _ok(rid, {"deleted": target})


@method("session.title")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    with _session_db(session) as db:
        if db is None:
            return _db_unavailable_error(rid, code=5007)
        key = session["session_key"]
        if "title" not in params:
            fallback = session.get("pending_title") or ""
            try:
                resolved_title = db.get_session_title(key) or ""
                if fallback:
                    if db.set_session_title(key, fallback):
                        session["pending_title"] = None
                        resolved_title = fallback
                    else:
                        existing_row = db.get_session(key)
                        existing_title = ((existing_row or {}).get("title") or "").strip()
                        if existing_title == fallback:
                            session["pending_title"] = None
                            resolved_title = fallback
                        elif not resolved_title:
                            resolved_title = fallback
                elif resolved_title:
                    session["pending_title"] = None
            except Exception:
                resolved_title = fallback
            _emit_session_info_for_session(params.get("session_id", ""), session)
            return _ok(
                rid,
                {
                    "title": resolved_title,
                    "session_key": key,
                },
            )
        title = (params.get("title", "") or "").strip()
        if not title:
            return _err(rid, 4021, "title required")
        try:
            if db.set_session_title(key, title):
                session["pending_title"] = None
                _emit_session_info_for_session(params.get("session_id", ""), session)
                return _ok(rid, {"pending": False, "title": title})
            # rowcount == 0 can mean "same value" as well as "missing row".
            existing_row = db.get_session(key)
            if existing_row:
                session["pending_title"] = None
                _emit_session_info_for_session(params.get("session_id", ""), session)
                return _ok(
                    rid,
                    {
                        "pending": False,
                        "title": (existing_row.get("title") or title),
                    },
                )
            # No row yet (the DB write is deferred to the first prompt so empty
            # drafts don't litter the sidebar). An explicit /title is clear user
            # intent, not an abandoned draft — so persist the row NOW and set the
            # title, mirroring the messaging gateway's _handle_title_command. The
            # old behavior only queued pending_title and relied on the post-turn
            # apply block; if that turn never landed under this session_key the
            # title was silently lost and the sidebar fell back to the message
            # preview. Creating the row up front removes that race entirely. The
            # min-messages sidebar filter keeps a titled 0-message row hidden, so
            # a /title'd-but-never-used draft still doesn't clutter the list.
            _ensure_session_db_row(session)
            with _session_db(session) as scoped_db:
                if scoped_db is not None and scoped_db.set_session_title(key, title):
                    session["pending_title"] = None
                    _emit_session_info_for_session(params.get("session_id", ""), session)
                    return _ok(rid, {"pending": False, "title": title})
            # Row creation didn't take (DB unavailable, or a concurrent writer) —
            # fall back to queuing so the post-turn apply block can still recover.
            session["pending_title"] = title
            _emit_session_info_for_session(params.get("session_id", ""), session)
            return _ok(rid, {"pending": True, "title": title})
        except ValueError as e:
            return _err(rid, 4022, str(e))
        except Exception as e:
            return _err(rid, 5007, str(e))


@method("message.react")
def _(rid, params: dict) -> dict:
    """Set or clear one author's emoji reaction on a persisted message.

    iOS Tapback semantics, enforced in the DB layer: one reaction per author
    per message, re-sending the same emoji retracts it. ``emoji: null`` clears
    unconditionally. ``row_id`` is the durable ``messages.id`` forwarded by
    ``_history_to_messages`` — the renderer's own message ids are ephemeral.
    """
    session, err = _sess_nowait(params, rid)
    if err:
        return err

    # A live message hasn't round-tripped through a resume, so the desktop has
    # no durable row id for it yet. It can instead name the ROLE whose newest
    # row it means — which is the message the user just reacted to.
    newest_role = str(params.get("newest_role") or "").strip()
    row_id = params.get("row_id")
    if row_id is None and newest_role not in {"user", "assistant"}:
        return _err(rid, 4023, "row_id or newest_role required")

    emoji = params.get("emoji")
    if emoji is not None:
        emoji = str(emoji).strip()
        if not emoji:
            return _err(rid, 4024, "emoji must be a non-empty string or null")

    author = str(params.get("author") or "user").strip()
    if author not in {"user", "agent"}:
        return _err(rid, 4025, "author must be 'user' or 'agent'")

    with _session_db(session) as db:
        if db is None:
            return _db_unavailable_error(rid, code=5007)
        try:
            if row_id is None:
                row_id = db.latest_message_row_id(
                    session["session_key"], role=newest_role
                )
                if row_id is None:
                    return _err(rid, 4040, "no message to react to yet")
            reactions = db.set_message_reaction(
                session["session_key"], int(row_id), emoji, author=author
            )
        except Exception as e:
            return _err(rid, 5007, str(e))

    if reactions is None:
        return _err(rid, 4040, "message not found in this session")

    return _ok(rid, {"row_id": int(row_id), "reactions": reactions})


@method("llm.oneshot")
def _(rid, params: dict) -> dict:
    """Run a single stateless LLM request outside any conversation.

    Generic helper for small generative chores (e.g. a commit message from a
    diff). Accepts either a named ``template`` + ``variables`` or an explicit
    ``instructions`` / ``input`` pair. When ``session_id`` resolves to a live
    session the call inherits that agent's model; otherwise it uses the
    configured auxiliary ``task`` backend. Never mutates session history, so
    prompt caching is untouched.
    """
    template = (params.get("template") or "").strip() or None
    instructions = params.get("instructions") or ""
    user_input = params.get("input") or ""
    variables = params.get("variables") if isinstance(params.get("variables"), dict) else {}
    task = (params.get("task") or "title_generation").strip() or "title_generation"

    try:
        max_tokens = int(params.get("max_tokens") or 1024)
    except (TypeError, ValueError):
        max_tokens = 1024
    temperature = params.get("temperature")
    if temperature is not None:
        try:
            temperature = float(temperature)
        except (TypeError, ValueError):
            temperature = None

    if not template and not str(instructions).strip() and not str(user_input).strip():
        return _err(rid, 4030, "llm.oneshot requires a template or instructions/input")

    # Optional: inherit the live session's model (no error if absent).
    session = _sessions.get(params.get("session_id") or "")
    main_runtime = _main_runtime_from_agent(session.get("agent")) if session else None

    try:
        from agent.oneshot import run_oneshot

        text = run_oneshot(
            instructions=instructions,
            user_input=user_input,
            template=template,
            variables=variables,
            task=task,
            max_tokens=max_tokens,
            temperature=temperature if temperature is not None else 0.3,
            main_runtime=main_runtime,
        )
    except KeyError as e:
        return _err(rid, 4031, str(e))
    except ValueError as e:
        return _err(rid, 4032, str(e))
    except Exception as e:
        logger.warning("llm.oneshot failed: %s", e)
        return _err(rid, 5030, f"one-shot generation failed: {e}")

    return _ok(rid, {"text": text})


@method("handoff.request")
def _(rid, params: dict) -> dict:
    """Queue a handoff of this session to a messaging platform.

    Desktop parity with the CLI ``/handoff`` command: we only write
    ``handoff_state='pending'`` onto the persisted session row. The actual
    transfer is performed by the separate ``hermes gateway`` process, whose
    ``_handoff_watcher`` claims the row, re-binds the session to the platform's
    home channel, and forges a synthetic turn. The desktop then polls
    ``handoff.state`` for the terminal result.
    """
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    if session.get("running"):
        return _err(
            rid,
            4009,
            "session busy — wait for the current turn to finish, then retry the handoff",
        )

    platform_name = (params.get("platform", "") or "").strip().lower()
    if not platform_name:
        return _err(rid, 4023, "platform required")

    # Validate against the live gateway config — an unconfigured platform or a
    # missing home channel would leave the handoff pending forever, so reject
    # up front with a clear, actionable message (mirrors cli.py).
    try:
        from gateway.config import Platform, load_gateway_config
    except Exception as e:  # pragma: no cover — gateway pkg always ships
        return _err(rid, 5021, f"could not load gateway config: {e}")
    try:
        platform = Platform(platform_name)
    except (ValueError, KeyError):
        return _err(rid, 4024, f"unknown platform '{platform_name}'")
    try:
        gw_config = load_gateway_config()
    except Exception as e:
        return _err(rid, 5021, f"could not load gateway config: {e}")
    pcfg = gw_config.platforms.get(platform)
    if not pcfg or not pcfg.enabled:
        return _err(
            rid,
            4025,
            f"platform '{platform_name}' is not configured/enabled in the gateway",
        )
    home = gw_config.get_home_channel(platform)
    if not home or not home.chat_id:
        return _err(
            rid,
            4026,
            f"no home channel configured for {platform_name} — set one with "
            "/sethome on the destination chat first",
        )

    # The watcher transfers a persisted DB row, so make sure one exists even
    # for a brand-new empty chat (mirrors the CLI's set_session_title stub).
    _ensure_session_db_row(session)

    with _session_db(session) as db:
        if db is None:
            return _db_unavailable_error(rid, code=5007)
        key = session["session_key"]
        try:
            if not db.get_session(key):
                db.set_session_title(key, f"handoff-{key[:8]}")
            ok = db.request_handoff(key, platform_name)
        except Exception as e:
            return _err(rid, 5007, str(e))

    if not ok:
        return _err(
            rid,
            4027,
            "session is already in flight for handoff — wait for it to settle, then retry",
        )
    return _ok(
        rid,
        {
            "queued": True,
            "session_key": key,
            "platform": platform_name,
            "home_name": home.name,
        },
    )


@method("handoff.state")
def _(rid, params: dict) -> dict:
    """Poll the handoff state for a session.

    Returns ``{state, platform, error}`` where ``state`` is one of
    ``pending|running|completed|failed`` (or empty when no handoff record
    exists). Desktop polls this after ``handoff.request``.
    """
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    with _session_db(session) as db:
        if db is None:
            return _db_unavailable_error(rid, code=5007)
        record = db.get_handoff_state(session["session_key"])

    record = record or {}
    return _ok(
        rid,
        {
            "state": record.get("state") or "",
            "platform": record.get("platform") or "",
            "error": record.get("error") or "",
        },
    )


@method("handoff.fail")
def _(rid, params: dict) -> dict:
    """Mark an in-flight handoff as failed so the user can retry.

    Desktop calls this when its bounded poll times out. Only pending/running
    rows are changed so a late success from the gateway watcher is not clobbered.
    """
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    reason = str(params.get("error") or "handoff failed").strip()[:500]
    with _session_db(session) as db:
        if db is None:
            return _db_unavailable_error(rid, code=5007)
        key = session["session_key"]
        record = db.get_handoff_state(key) or {}
        state = record.get("state") or ""
        if state in {"pending", "running"}:
            db.fail_handoff(key, reason)
            return _ok(rid, {"failed": True, "state": "failed"})

    return _ok(rid, {"failed": False, "state": state})


@method("session.usage")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    if session.get("cxba_binding") is not None:
        from tui_gateway.cxba_runtime import run_for_session

        active_run = run_for_session(str(params.get("session_id") or ""))
        if active_run is None or active_run.status in {
            "COMPLETED", "SAFE_STOPPED", "FORCE_STOPPED", "FAILED"
        }:
            return _err(rid, 4095, "CXBA Run is no longer active")
        if active_run.status == "SAFE_STOPPING":
            return _err(rid, 4094, "Run is safe-stopping")
        if not session.get("running"):
            with session["history_lock"]:
                if session.get("running"):
                    return _err(rid, 4009, "session became busy")
                session["running"] = True
            _run_prompt_submit(
                rid,
                str(params.get("session_id") or ""),
                session,
                text,
                trusted_run_context=active_run.context,
            )
            return _ok(rid, {"status": "continuing", "text": text})
    agent = session.get("agent")
    usage: dict = _session_usage_snapshot(session)
    if agent is None and not usage:
        usage = {"calls": 0, "input": 0, "output": 0, "total": 0}
    # Nous credits block — agent-independent (a portal fetch), so it shows even
    # with zero API calls or on a resumed session. The TUI /usage panel renders
    # these lines regardless of `calls`. Fail-open: [] when not logged into Nous
    # or on any portal hiccup.
    try:
        from agent.account_usage import nous_credits_lines

        credits = nous_credits_lines()
        if credits:
            usage["credits_lines"] = credits
    except Exception:
        pass
    return _ok(rid, usage)


@method("session.context_breakdown")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    if session.get("cxba_binding") is not None:
        from tui_gateway.cxba_runtime import run_for_session

        active_run = run_for_session(str(params.get("session_id") or ""))
        if active_run is None or active_run.status in {
            "COMPLETED", "SAFE_STOPPED", "FORCE_STOPPED", "FAILED"
        }:
            return _err(rid, 4095, "CXBA Run is no longer active")
        if active_run.status == "SAFE_STOPPING":
            return _err(rid, 4094, "Run is safe-stopping")
    agent = session.get("agent")
    if agent is None:
        usage = _session_usage_snapshot(session) or _get_usage(None)
        return _ok(
            rid,
            {
                "categories": [],
                "context_max": usage.get("context_max", 0) or 0,
                "context_percent": usage.get("context_percent", 0) or 0,
                "context_used": usage.get("context_used", 0) or 0,
                "estimated_total": usage.get("context_used", 0) or usage.get("total", 0) or 0,
                "model": _metadata_mirror(session).get("model", ""),
            },
        )
    with session["history_lock"]:
        history = list(session.get("history", []))
    try:
        from agent.context_breakdown import compute_session_context_breakdown

        payload = compute_session_context_breakdown(agent, history)
    except Exception as exc:
        return _err(rid, 5000, f"Could not compute context breakdown: {exc}")
    return _ok(rid, payload)


@method("pet.info")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Return the active petdex pet for surfaces that render sprites.

    Shared by the desktop (canvas) and the TUI (half-block). Carries the
    spritesheet bytes (base64) plus the engine's frame geometry + state-row
    taxonomy so the renderer is a thin, framework-native consumer. The
    activity→state decision is mirrored from ``agent.pet.state`` client-side.

    Agent-independent (reads config + disk), so it works on any session and
    before the agent finishes building. Fail-open: returns ``enabled=False``
    on any error rather than erroring the surface.
    """
    try:
        enabled, pet, scale = _pet_active_selection()

        if not enabled or pet is None or not pet.exists:
            return _ok(rid, {"enabled": False})

        return _ok(rid, {"enabled": True, **_pet_sprite_payload(pet, scale=scale)})
    except Exception as exc:  # noqa: BLE001 - cosmetic, never break the surface
        logger.debug("pet.info failed: %s", exc)
        return _ok(rid, {"enabled": False})


@method("pet.info.meta")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Cheap active-pet metadata used to avoid full payload refreshes."""
    try:
        enabled, pet, scale = _pet_active_selection()
        if not enabled or pet is None or not pet.exists:
            return _ok(rid, {"enabled": False})
        return _ok(
            rid,
            {
                "enabled": True,
                "slug": pet.slug,
                "displayName": pet.display_name,
                "scale": scale,
                "spritesheetRevision": _pet_sheet_revision(pet.spritesheet),
            },
        )
    except Exception as exc:  # noqa: BLE001 - cosmetic, never break the surface
        logger.debug("pet.info.meta failed: %s", exc)
        return _ok(rid, {"enabled": False})


@method("pet.cells")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Return half-block cell frames for one pet state (TUI renderer).

    The TUI can't draw a canvas, so the engine downsamples the spritesheet to
    a grid of half-block cells and the Ink side paints them with native color
    props. Each cell is ``[tr,tg,tb,ta, br,bg,bb,ba]`` (top + bottom pixel).

    Params: ``state`` (idle/run/review/failed/wave/jump), ``cols`` (width).
    Fail-open: ``enabled=False`` on any problem.
    """
    try:
        from agent.pet import constants, render, store
        from agent.pet.render import PetRenderer

        try:
            from hermes_cli.config import load_config

            cfg = load_config()
            display = cfg.get("display", {}) if isinstance(cfg.get("display"), dict) else {}
            pet_cfg = display.get("pet", {}) if isinstance(display.get("pet"), dict) else {}
        except Exception:
            pet_cfg = {}

        if not bool(pet_cfg.get("enabled")):
            return _ok(rid, {"enabled": False})

        pet = store.resolve_active_pet(str(pet_cfg.get("slug", "") or ""))
        if pet is None or not pet.exists:
            return _ok(rid, {"enabled": False})

        state = str(params.get("state") or constants.PetState.IDLE.value)
        scale = float(pet_cfg.get("scale", constants.DEFAULT_SCALE) or constants.DEFAULT_SCALE)
        cols = int(params.get("cols") or 0) or constants.resolve_cols(scale, pet_cfg.get("unicode_cols", 0))

        # Graphics path: when the TUI is attached to a real TTY (``graphics``)
        # and the terminal speaks the kitty protocol, return a Unicode-
        # placeholder payload for a crisp image instead of half-blocks. Env
        # detection (KITTY_WINDOW_ID / TERM / TERM_PROGRAM) is shared with the
        # Ink process since it spawns us; the dashboard PTY (xterm.js) has no
        # such env, so it falls through to half-blocks automatically. Only
        # kitty is grid-safe in Ink — iTerm/sixel stay on the fallback.
        if params.get("graphics"):
            configured = str(pet_cfg.get("render_mode", "auto") or "auto").lower()
            gmode = render.detect_terminal_graphics() if configured in ("", "auto") else configured
            if gmode == "kitty":
                image_id = render.kitty_image_id(pet.slug)
                # kitty sizes from scaled pixels (_cell_box), so unicode_cols is moot here.
                payload = PetRenderer(
                    str(pet.spritesheet), mode="kitty", scale=scale
                ).kitty_payload(state, image_id=image_id)
                if payload:
                    kcount = len(payload["frames"]) or 1
                    return _ok(
                        rid,
                        {
                            "enabled": True,
                            "slug": pet.slug,
                            "displayName": pet.display_name,
                            "state": state,
                            "graphics": "kitty",
                            "imageId": image_id,
                            "color": render.kitty_color_hex(image_id),
                            "cols": payload["cols"],
                            "rows": payload["rows"],
                            "placeholder": payload["placeholder"],
                            "frames": payload["frames"],
                            "frameMs": constants.LOOP_MS / max(1, kcount),
                            "scale": scale,
                        },
                    )

        renderer = PetRenderer(
            str(pet.spritesheet),
            mode="unicode",
            scale=scale,
            unicode_cols=cols,
        )
        count = renderer.frame_count(state) or 1
        frames = []
        for i in range(count):
            grid = renderer.cells(state, i, cols=cols)
            frames.append(
                [[[*top, *bottom] for (top, bottom) in row] for row in grid]
            )

        return _ok(
            rid,
            {
                "enabled": True,
                "slug": pet.slug,
                "displayName": pet.display_name,
                "state": state,
                "cols": cols,
                "frameMs": constants.LOOP_MS / max(1, count),
                "frames": frames,
                "scale": scale,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("pet.cells failed: %s", exc)
        return _ok(rid, {"enabled": False})


@method("pet.gallery")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """List adoptable pets for the desktop appearance picker.

    Returns the petdex gallery merged with local install state plus the
    current config (active slug + enabled). Agent-independent. Fail-open:
    returns whatever is installed locally if the gallery can't be reached, so
    the picker still works offline.

    Param ``localOnly`` (bool): skip the remote petdex manifest fetch and return
    only locally-installed pets. The desktop loads this first so the user's own
    pets render instantly instead of waiting on the (possibly slow) manifest.
    """
    local_only = bool(params.get("localOnly"))
    try:
        from agent.pet import store

        try:
            from hermes_cli.config import load_config

            cfg = load_config()
            display = cfg.get("display", {}) if isinstance(cfg.get("display"), dict) else {}
            pet_cfg = display.get("pet", {}) if isinstance(display.get("pet"), dict) else {}
        except Exception:
            pet_cfg = {}

        installed = {p.slug: p for p in store.installed_pets()}

        gallery: list[dict] = []
        seen: set[str] = set()
        try:
            from agent.pet.manifest import fetch_manifest, prefetch

            # Local-only: skip the network entirely, but kick off a background
            # warm so the follow-up full request usually hits a cached manifest.
            if local_only:
                prefetch()

            for entry in [] if local_only else fetch_manifest():
                seen.add(entry.slug)
                gallery.append(
                    {
                        "slug": entry.slug,
                        "displayName": entry.display_name,
                        "installed": entry.slug in installed,
                        "spritesheetUrl": entry.spritesheet_url,
                        # petdex exposes no popularity metric; "curated" (its
                        # hand-picked/official set, identified by the asset path)
                        # is the closest signal, so the picker can surface it first.
                        "curated": "/curated/" in entry.spritesheet_url,
                        "generated": entry.slug in installed and installed[entry.slug].generated,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - offline: fall back to installed
            logger.debug("pet.gallery manifest fetch failed: %s", exc)

        # Always include locally-installed pets even if the gallery is unreachable.
        for slug, pet in installed.items():
            if slug not in seen:
                gallery.append(
                    {
                        "slug": slug,
                        "displayName": pet.display_name,
                        "installed": True,
                        "spritesheetUrl": "",
                        "generated": pet.generated,
                    }
                )

        return _ok(
            rid,
            {
                "enabled": bool(pet_cfg.get("enabled")),
                "active": str(pet_cfg.get("slug", "") or ""),
                "pets": gallery,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("pet.gallery failed: %s", exc)
        return _ok(rid, {"enabled": False, "active": "", "pets": []})


@method("pet.select")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Adopt a pet from the desktop picker: install (if needed) + activate.

    Params: ``slug`` (required). Writes ``display.pet.*`` to config and returns
    ``{ok, slug, displayName}``. The surface re-pulls ``pet.info`` to render it.
    """
    slug = str(params.get("slug") or "").strip()
    if not slug:
        return _err(rid, 4004, "missing slug")
    try:
        from agent.pet import store
        from agent.pet.manifest import ManifestError
        from hermes_cli.pets import _set_active

        try:
            pet = store.install_pet(slug)
        except (store.PetStoreError, ManifestError) as exc:
            return _err(rid, 5031, f"could not adopt '{slug}': {exc}")
        _set_active(slug)
        return _ok(rid, {"ok": True, "slug": slug, "displayName": pet.display_name})
    except Exception as exc:  # noqa: BLE001
        logger.debug("pet.select failed: %s", exc)
        return _err(rid, 5031, f"pet.select failed: {exc}")


@method("pet.remove")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Uninstall a pet from the desktop picker (delete its on-disk directory).

    Params: ``slug`` (required). If the removed pet was the active one, the
    display is turned off so nothing tries to render a now-missing sprite.
    Returns ``{ok, slug}`` where ``ok`` reflects whether a directory was deleted.
    """
    slug = str(params.get("slug") or "").strip()
    if not slug:
        return _err(rid, 4004, "missing slug")
    try:
        from agent.pet import store
        from hermes_cli.pets import _clear_active_if

        removed = store.remove_pet(slug)

        # If that was the active pet, stop surfaces pointing at a deleted sprite.
        try:
            _clear_active_if(slug)
        except Exception as exc:  # noqa: BLE001 - removal already succeeded
            logger.debug("pet.remove config update failed: %s", exc)

        return _ok(rid, {"ok": removed, "slug": slug})
    except Exception as exc:  # noqa: BLE001
        logger.debug("pet.remove failed: %s", exc)
        return _err(rid, 5031, f"pet.remove failed: {exc}")


@method("pet.export")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Export an installed pet as a re-importable ``.zip`` (pet.json + sprite).

    Params: ``slug`` (required). Returns ``{ok, filename, zipBase64}`` — the
    client decodes the base64 and saves it. Heavy-ish (reads + zips files) but
    small; runs inline.
    """
    slug = str(params.get("slug") or "").strip()
    if not slug:
        return _err(rid, 4004, "missing slug")
    try:
        import base64

        from agent.pet import store

        filename, data = store.export_pet(slug)
        return _ok(
            rid,
            {"ok": True, "filename": filename, "zipBase64": base64.standard_b64encode(data).decode("ascii")},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("pet.export failed: %s", exc)
        return _err(rid, 5031, f"pet.export failed: {exc}")


@method("pet.rename")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Rename an installed pet's display name + realign its slug/dir.

    Params: ``slug`` + ``name`` (both required). Lets the generate flow hatch
    with a provisional name and apply the user's chosen name at adopt time.
    Returns ``{ok, slug, displayName}`` with the (possibly new) slug.
    """
    slug = str(params.get("slug") or "").strip()
    name = str(params.get("name") or "").strip()
    if not slug:
        return _err(rid, 4004, "missing slug")
    if not name:
        return _err(rid, 4004, "missing name")
    try:
        from agent.pet import store

        new_slug = store.rename_pet(slug, name)
        if not new_slug:
            return _err(rid, 5031, "pet.rename failed")

        # The dir may have moved; if the renamed pet was active, follow the slug
        # in config so surfaces don't point at the old (now-missing) directory.
        if new_slug != slug:
            try:
                from hermes_cli.pets import _rename_active_if

                _rename_active_if(slug, new_slug)
            except Exception as exc:  # noqa: BLE001 - rename already succeeded
                logger.debug("pet.rename config update failed: %s", exc)

        return _ok(rid, {"ok": True, "slug": new_slug, "displayName": name})
    except Exception as exc:  # noqa: BLE001
        logger.debug("pet.rename failed: %s", exc)
        return _err(rid, 5031, f"pet.rename failed: {exc}")


@method("pet.thumb")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Return a small idle-frame PNG (data URI) for one pet — the picker preview.

    Cropped + cached server-side so the renderer gets a same-origin data URL
    instead of a CDN ``<img>`` (which the desktop CSP / R2 hotlink rules break).
    Params: ``slug`` (required), ``url`` (optional petdex spritesheet URL used
    only for not-yet-installed pets). Fail-open: ``{ok: false}`` with no error.
    """
    slug = str(params.get("slug") or "").strip()
    if not slug:
        return _err(rid, 4004, "missing slug")
    try:
        import base64

        from agent.pet import store

        data = store.thumbnail_png(slug, source_url=str(params.get("url") or ""))
        if not data:
            return _ok(rid, {"ok": False, "slug": slug})

        return _ok(
            rid,
            {
                "ok": True,
                "slug": slug,
                "dataUri": "data:image/png;base64," + base64.standard_b64encode(data).decode("ascii"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("pet.thumb failed: %s", exc)
        return _ok(rid, {"ok": False, "slug": slug})


@method("pet.disable")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Turn the pet off from the desktop picker (``display.pet.enabled=false``)."""
    try:
        from hermes_cli.pets import _set_enabled

        _set_enabled(False)
        return _ok(rid, {"ok": True})
    except Exception as exc:  # noqa: BLE001
        logger.debug("pet.disable failed: %s", exc)
        return _err(rid, 5031, f"pet.disable failed: {exc}")


@method("pet.scale")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Persist ``display.pet.scale`` from the desktop slider. Params: ``scale``.

    Clamped to the engine bounds. The renderer updates its own ``$petInfo`` for
    instant feedback; this just makes the change durable + visible to the other
    terminal surfaces on their next read.
    """
    try:
        from hermes_cli.pets import set_pet_scale

        scale, err = set_pet_scale(params.get("scale"))
        if err:
            return _err(rid, 4004, err)
        return _ok(rid, {"ok": True, "scale": scale})
    except Exception as exc:  # noqa: BLE001
        logger.debug("pet.scale failed: %s", exc)
        return _err(rid, 5031, f"pet.scale failed: {exc}")


@method("pet.cancel")
def _(rid, params: dict) -> dict:
    """Signal an in-flight ``pet.generate``/``pet.hatch`` (by token) to stop.

    Best-effort + idempotent: cancelling an unknown/finished token is a no-op.
    Stays off the worker pool so it lands while a heavy generation is occupying
    it. Returns ``{ok: True}``.
    """
    token = str(params.get("token") or "").strip()
    if token:
        _pet_cancel_request(token)
    return _ok(rid, {"ok": True})


@method("pet.generate.status")
def _(rid, params: dict) -> dict:
    """Whether pet generation is possible right now.

    True only when a reference-capable image backend (Nous Portal / OpenRouter /
    OpenAI gpt-image) is configured — the desktop checks this on open so it can
    offer setup instead of a dead prompt. Cheap (config + plugin discovery).
    """
    try:
        from agent.pet.generate.imagegen import (
            GenerationError,
            list_sprite_providers,
            resolve_provider,
        )

        try:
            resolve_provider(require_references=True)
            available = True
        except GenerationError:
            available = False
        try:
            providers = list_sprite_providers()
        except Exception as exc:  # noqa: BLE001 - picker is best-effort
            logger.debug("pet provider list failed: %s", exc)
            providers = []
        return _ok(rid, {"available": available, "providers": providers})
    except Exception as exc:  # noqa: BLE001 - never break the surface
        logger.debug("pet.generate.status failed: %s", exc)
        return _ok(rid, {"available": False, "providers": []})


@method("pet.generate")
def _(rid, params: dict) -> dict:
    """Generate candidate base looks for a new pet (the draft/variant step).

    Params: ``prompt`` (required unless ``referenceImage`` is given), ``count``
    (default 4), ``style`` (default ``auto``), ``referenceImage`` (optional data
    URL — a user photo/reference every draft is grounded on, e.g. to make *their*
    pet). Returns ``{ok, token, drafts:[{index, dataUri}]}`` — the token keys the
    staged base images for a later ``pet.hatch``. Heavy (network): worker pool.
    """
    prompt = str(params.get("prompt") or "").strip()
    ref_raw = str(params.get("referenceImage") or "").strip()
    if not prompt and not ref_raw:
        return _err(rid, 4004, "missing prompt")
    try:
        count = max(1, min(4, int(params.get("count") or 4)))
    except (TypeError, ValueError):
        count = 4
    style = str(params.get("style") or "auto").strip() or "auto"

    try:
        import shutil
        import uuid

        from agent.pet.generate import generate_base_drafts
        from agent.pet.generate.imagegen import GenerationError, resolve_provider

        root = _pet_gen_root()
        _pet_gen_sweep(root)

        # Token up front so each draft can be staged + streamed the moment it
        # lands, instead of the user staring at a blank grid until all N finish.
        token = uuid.uuid4().hex[:12]
        _pet_cancel_arm(token)
        stage = root / token
        stage.mkdir(parents=True, exist_ok=True)

        reference_images = None
        if ref_raw:
            try:
                reference_images = _pet_reference_images_from_data_url(ref_raw, stage)
            except ValueError as exc:
                _pet_cancel_release(token)
                return _err(rid, 4004, str(exc))

        # Optional desktop picker override: resolve the chosen provider up front so
        # a bad/uncredentialed pick fails fast instead of mid-fan-out.
        provider_name = str(params.get("provider") or "").strip()
        sprite = None
        if provider_name:
            try:
                sprite = resolve_provider(require_references=bool(reference_images), prefer=provider_name)
            except GenerationError as exc:
                _pet_cancel_release(token)
                return _err(rid, 5031, str(exc))

        concept = prompt or "a pet based on the reference image"
        out: list[dict] = []

        # Hand the token to the client up front (token-only init event) so a Stop
        # fired before the first draft lands can still target this run.
        try:
            _emit("pet.generate.progress", "", {"token": token, "count": count})
        except Exception as exc:  # noqa: BLE001 - streaming is best-effort
            logger.debug("pet.generate init emit failed: %s", exc)

        def _on_draft(index: int, src) -> None:
            dest = stage / f"draft-{index}.png"
            try:
                shutil.copyfile(src, dest)
                data_uri = _pet_png_data_uri(dest)
            except Exception as exc:  # noqa: BLE001 - skip a bad draft, keep the rest
                logger.debug("pet.generate draft %d failed: %s", index, exc)
                return
            out.append({"index": index, "dataUri": data_uri})
            # Stream this draft to the client so the grid fills in live. Best-
            # effort: a transport hiccup must not abort the generation itself.
            try:
                _emit(
                    "pet.generate.progress",
                    "",
                    {"token": token, "index": index, "dataUri": data_uri, "count": count},
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("pet.generate progress emit failed: %s", exc)

        try:
            generate_base_drafts(
                concept,
                n=count,
                style=style,
                reference_images=reference_images,
                provider=sprite,
                on_draft=_on_draft,
                is_cancelled=lambda: _pet_is_cancelled(token),
            )
        except GenerationError as exc:
            _pet_cancel_release(token)
            return _err(rid, 5031, str(exc))

        cancelled = _pet_is_cancelled(token)
        _pet_cancel_release(token)
        if cancelled:
            return _err(rid, 5031, "generation cancelled")
        if not out:
            return _err(rid, 5031, "generation produced no usable drafts")
        out.sort(key=lambda d: d["index"])
        return _ok(rid, {"ok": True, "token": token, "drafts": out})
    except Exception as exc:  # noqa: BLE001
        logger.debug("pet.generate failed: %s", exc)
        return _err(rid, 5031, f"pet.generate failed: {exc}")


@method("pet.hatch")
def _(rid, params: dict) -> dict:
    """Turn a chosen base draft into a full pet — installed but NOT yet active.

    Generation is expensive and the result varies, so hatch produces a *preview*
    the surface plays (all frames) before the user commits: the pet is written to
    the store (so it can be rendered + later activated) but the active pet is left
    untouched. Adopt with ``pet.select`` or throw it away with ``pet.remove``.

    Params: ``token`` + ``index`` (from ``pet.generate``), ``name`` (required),
    ``description`` (optional), ``prompt`` (optional concept for row prompts),
    ``style`` (optional). Returns ``{ok, slug, displayName, warnings, pet}`` where
    ``pet`` is the renderer payload. Heavy (network + raster): worker pool.
    """
    token = str(params.get("token") or "").strip()
    # Hatch cancellation rides its own key, not the generation token: hatching a
    # draft mid-generation means pet.generate is still releasing `token`, which
    # would otherwise wipe the arm we set here. Falls back to `token` for clients
    # that don't send one.
    cancel_token = str(params.get("cancelToken") or "").strip() or token
    index = params.get("index", 0)
    name = str(params.get("name") or "").strip()
    if not token:
        return _err(rid, 4004, "missing token")
    if not name:
        return _err(rid, 4004, "missing name")
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = 0

    try:
        from agent.pet import store
        from agent.pet.generate import hatch_pet
        from agent.pet.generate.imagegen import GenerationError, resolve_provider

        base = _pet_gen_root() / token / f"draft-{index}.png"
        if not base.is_file():
            return _err(rid, 4004, "draft expired — generate again")

        # Optional desktop picker override (rows always need reference grounding).
        provider_name = str(params.get("provider") or "").strip()
        sprite = None
        if provider_name:
            try:
                sprite = resolve_provider(require_references=True, prefer=provider_name)
            except GenerationError as exc:
                return _err(rid, 5031, str(exc))

        _pet_cancel_arm(cancel_token)
        slug = store.unique_slug(name)

        def _on_progress(event: str, detail: str) -> None:
            # Row progress is encoded as "<state>:<done>:<total>" so the egg
            # screen can show "Drawing <state>… (n/total)"; other phases
            # (compose, save) pass through as-is. Best-effort streaming.
            payload: dict = {"event": event, "detail": detail}
            if event == "row" and detail.count(":") == 2:
                state, done, total = detail.split(":")
                payload = {"event": "row", "state": state, "done": done, "total": total}
            try:
                _emit("pet.hatch.progress", "", payload)
            except Exception as exc:  # noqa: BLE001
                logger.debug("pet.hatch progress emit failed: %s", exc)

        try:
            result = hatch_pet(
                base_image=base,
                slug=slug,
                display_name=name,
                description=str(params.get("description") or ""),
                concept=str(params.get("prompt") or name),
                style=str(params.get("style") or "auto").strip() or "auto",
                provider=sprite,
                on_progress=_on_progress,
                is_cancelled=lambda: _pet_is_cancelled(cancel_token),
            )
        except GenerationError as exc:
            return _err(rid, 5031, str(exc))
        finally:
            _pet_cancel_release(cancel_token)

        pet = store.load_pet(result.slug)
        payload = _pet_sprite_payload(pet, scale=_pet_config_scale()) if pet else {}
        return _ok(
            rid,
            {
                "ok": True,
                "slug": result.slug,
                "displayName": result.display_name,
                "warnings": result.validation.get("warnings", []),
                "pet": payload,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("pet.hatch failed: %s", exc)
        return _err(rid, 5031, f"pet.hatch failed: {exc}")


@method("billing.state")
def _(rid, params: dict) -> dict:
    """GET /api/billing/state → serialized BillingState (Screen 1 + 5).

    Fail-open like the other billing RPCs: a logged-out / unreachable portal yields
    {ok:true, logged_in:false}. No scope required for this endpoint.
    """
    try:
        from agent.billing_view import build_billing_state

        state = build_billing_state()
        return _ok(rid, _serialize_billing_state(state))
    except Exception:
        return _ok(rid, {"ok": True, "logged_in": False, "error": "could not load billing state"})


@method("usage.bars")
def _(rid, params: dict) -> dict:
    """Shared dollar usage model (two-bar view) for /usage + /subscription.

    Fail-open: logged-out / unreachable portal → {ok:true, available:false}.
    No scope required (read-only).
    """
    try:
        from agent.billing_usage import build_usage_model

        return _ok(rid, _serialize_usage_model(build_usage_model()))
    except Exception:
        return _ok(rid, {"ok": True, "available": False})


@method("subscription.state")
def _(rid, params: dict) -> dict:
    """GET /api/billing/subscription → serialized SubscriptionState.

    Fail-open like billing.state: logged-out / unreachable portal →
    {ok:true, logged_in:false}. No scope required (read-only).
    """
    try:
        from agent.subscription_view import build_subscription_state

        state = build_subscription_state()
        return _ok(rid, _serialize_subscription_state(state))
    except Exception:
        return _ok(rid, {"ok": True, "logged_in": False, "error": "could not load subscription state"})


@method("subscription.preview")
def _(rid, params: dict) -> dict:
    """POST /api/billing/subscription/preview → serialized quote or typed error.

    params: {subscription_type_id: str}. Chargeless effect quote. Requires
    billing:manage (live Stripe calls + amounts), so a 403 → insufficient_scope
    drives the device step-up exactly like the mutations.
    """
    from agent.subscription_view import subscription_change_preview_from_payload
    from hermes_cli.nous_billing import BillingError, post_subscription_preview

    tier_id = params.get("subscription_type_id")
    if not tier_id:
        return _ok(rid, {"ok": False, "error": "invalid_request", "message": "subscription_type_id is required"})
    try:
        preview = subscription_change_preview_from_payload(
            post_subscription_preview(subscription_type_id=tier_id)
        )
        return _ok(rid, _serialize_subscription_preview(preview))
    except BillingError as exc:
        return _ok(rid, _serialize_billing_error(exc))
    except Exception as exc:
        return _ok(rid, {"ok": False, "error": "error", "message": str(exc)})


@method("subscription.change")
def _(rid, params: dict) -> dict:
    """PUT /api/billing/subscription/pending-change → {ok, message} or typed error.

    params: {subscription_type_id?: str, cancel?: bool}. Schedules a downgrade /
    same-price change OR a cancellation at period end (chargeless). Requires
    billing:manage.
    """
    from hermes_cli.nous_billing import BillingError, put_subscription_pending_change

    cancel = bool(params.get("cancel"))
    tier_id = params.get("subscription_type_id")
    if not cancel and not tier_id:
        return _ok(rid, {"ok": False, "error": "invalid_request", "message": "subscription_type_id or cancel is required"})
    try:
        result = put_subscription_pending_change(subscription_type_id=tier_id, cancel=cancel)
        return _ok(rid, {"ok": True, "message": result.get("message"), "payload": result})
    except BillingError as exc:
        return _ok(rid, _serialize_billing_error(exc))
    except Exception as exc:
        return _ok(rid, {"ok": False, "error": "error", "message": str(exc)})


@method("subscription.resume")
def _(rid, params: dict) -> dict:
    """DELETE /api/billing/subscription/pending-change → {ok, message} or typed error.

    Clears a scheduled downgrade or cancellation (resume / undo). Chargeless, but it
    re-enables recurring spend → requires billing:manage and honors the kill-switch.
    """
    from hermes_cli.nous_billing import BillingError, delete_subscription_pending_change

    try:
        result = delete_subscription_pending_change()
        return _ok(rid, {"ok": True, "message": result.get("message"), "payload": result})
    except BillingError as exc:
        return _ok(rid, _serialize_billing_error(exc))
    except Exception as exc:
        return _ok(rid, {"ok": False, "error": "error", "message": str(exc)})


@method("subscription.upgrade")
def _(rid, params: dict) -> dict:
    """POST /api/billing/subscription/upgrade → {ok, status, ...} or typed error.

    params: {subscription_type_id: str, idempotency_key?: str}. The single money
    route: prorate + charge the card on the subscription + flip the plan. SCA /
    decline come back as status requires_action / payment_failed with a recovery_url
    to finish in the portal. The idempotency key is minted if absent and echoed so
    the TUI reuses it on retry of the SAME upgrade. Requires billing:manage.
    """
    from agent.billing_view import new_idempotency_key
    from hermes_cli.nous_billing import BillingError, post_subscription_upgrade

    tier_id = params.get("subscription_type_id")
    if not tier_id:
        return _ok(rid, {"ok": False, "error": "invalid_request", "message": "subscription_type_id is required"})
    key = params.get("idempotency_key") or new_idempotency_key()
    try:
        result = post_subscription_upgrade(subscription_type_id=tier_id, idempotency_key=key)
        return _ok(
            rid,
            {
                "ok": True,
                "status": result.get("status"),
                "target_tier_name": result.get("targetTierName"),
                "recovery_url": result.get("recoveryUrl"),
                "reason": result.get("reason"),
                "idempotency_key": key,
            },
        )
    except BillingError as exc:
        env = _serialize_billing_error(exc)
        env["idempotency_key"] = key  # so the TUI can reuse on retry
        return _ok(rid, env)
    except Exception as exc:
        return _ok(rid, {"ok": False, "error": "error", "message": str(exc), "idempotency_key": key})


@method("billing.charge")
def _(rid, params: dict) -> dict:
    """POST /api/billing/charge → {ok, chargeId} or a typed error envelope.

    params: {amount_usd: str|number, idempotency_key?: str}. If no key is
    supplied, the server-side core mints a fresh one and returns it so the TUI can
    reuse it on retry of the SAME purchase.
    """
    from hermes_cli.nous_billing import BillingError, post_charge
    from agent.billing_view import new_idempotency_key

    amount = params.get("amount_usd")
    if amount is None:
        return _ok(rid, {"ok": False, "error": "invalid_request", "message": "amount_usd is required"})
    key = params.get("idempotency_key") or new_idempotency_key()
    try:
        result = post_charge(amount_usd=amount, idempotency_key=key)
        return _ok(rid, {"ok": True, "charge_id": result.get("chargeId"), "idempotency_key": key})
    except BillingError as exc:
        env = _serialize_billing_error(exc)
        env["idempotency_key"] = key  # so the TUI can reuse on retry
        return _ok(rid, env)
    except Exception as exc:
        return _ok(rid, {"ok": False, "error": "error", "message": str(exc), "idempotency_key": key})


@method("billing.charge_status")
def _(rid, params: dict) -> dict:
    """GET /api/billing/charge/{id} → {ok, status, ...} or typed error.

    The poll. Caller drives the 2s/5-min cadence; this is a single status read.
    """
    from hermes_cli.nous_billing import BillingError, get_charge_status

    charge_id = params.get("charge_id")
    if not charge_id:
        return _ok(rid, {"ok": False, "error": "invalid_charge_id", "message": "charge_id is required"})
    try:
        result = get_charge_status(charge_id)
        return _ok(
            rid,
            {
                "ok": True,
                "status": result.get("status"),
                "amount_usd": result.get("amountUsd"),
                "settled_at": result.get("settledAt"),
                "reason": result.get("reason"),
            },
        )
    except BillingError as exc:
        return _ok(rid, _serialize_billing_error(exc))
    except Exception as exc:
        return _ok(rid, {"ok": False, "error": "error", "message": str(exc)})


@method("billing.auto_reload")
def _(rid, params: dict) -> dict:
    """PATCH /api/billing/auto-top-up → {ok:true} or typed error (Screen 2).

    params: {enabled: bool, threshold: number, top_up_amount: number}.
    """
    from hermes_cli.nous_billing import BillingError, patch_auto_top_up

    try:
        enabled = bool(params.get("enabled"))
        threshold = params.get("threshold")
        top_up_amount = params.get("top_up_amount")
        if threshold is None or top_up_amount is None:
            return _ok(rid, {"ok": False, "error": "invalid_request", "message": "threshold and top_up_amount are required"})
        patch_auto_top_up(enabled=enabled, threshold=threshold, top_up_amount=top_up_amount)
        return _ok(rid, {"ok": True})
    except BillingError as exc:
        return _ok(rid, _serialize_billing_error(exc))
    except Exception as exc:
        return _ok(rid, {"ok": False, "error": "error", "message": str(exc)})


@method("billing.step_up")
def _(rid, params: dict) -> dict:
    """Run the lazy billing:manage step-up device flow → {ok, granted}.

    Triggered by the TUI after a billing call returns error=insufficient_scope.
    Returns granted:false when the server silently downscopes (non-admin / unticked).

    Runs on the thread pool (in _LONG_HANDLERS): the device flow blocks for the
    whole device-code lifetime (minutes), so it must not stall the main stdin loop.
    The verification URL/code reach the TUI via an out-of-band ``billing.step_up.
    verification`` event (a plain print would be dropped by the JSON-RPC stdout
    pipe), and the browser is opened TUI-side via openExternalUrl — never with the
    gateway's headless webbrowser.open (hence open_browser=False).
    """
    sid = params.get("session_id") or ""
    try:
        from hermes_cli.auth import step_up_nous_billing_scope
        from hermes_cli.nous_billing import BillingError

        def _on_verification(url: str, code: str) -> None:
            _emit(
                "billing.step_up.verification",
                sid,
                {"verification_url": url, "user_code": code},
            )

        granted = step_up_nous_billing_scope(
            open_browser=False, on_verification=_on_verification
        )
        return _ok(rid, {"ok": True, "granted": bool(granted)})
    except BillingError as exc:
        # Route typed billing errors (e.g. session_revoked when the token expires
        # mid-device-flow) through the shared spine like the other write handlers,
        # so the TUI maps them to the right copy instead of a generic failure.
        env = _serialize_billing_error(exc)
        env["granted"] = False
        return _ok(rid, env)
    except Exception as exc:
        return _ok(rid, {"ok": False, "error": "error", "message": str(exc), "granted": False})


@method("session.status")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err

    from hermes_constants import display_hermes_home

    key = session.get("session_key") or params.get("session_id") or ""
    agent = session.get("agent")
    meta = {}
    # Prefer the live session's bound profile db, else params.profile, else launch.
    status_params = dict(params or {})
    if not status_params.get("profile") and session.get("profile_home"):
        # profile_home is a path; still allow _session_db via a synthetic session
        pass
    with _session_db(session) as db:
        if db is None:
            # Fall back to ~params.profile naming for not-yet-mapped sessions.
            with _profile_db(params) as db2:
                db = db2
                if db and key:
                    try:
                        meta = db.get_session(key) or {}
                    except Exception:
                        meta = {}
                db = None  # prevent double-use
        if db is not None and key:
            try:
                meta = db.get_session(key) or {}
            except Exception:
                meta = {}

    def _dt(value, fallback: datetime | None = None) -> datetime:
        if value:
            try:
                return datetime.fromtimestamp(float(value))
            except Exception:
                pass
        return fallback or datetime.now()

    created = _dt(meta.get("started_at"))
    updated = created
    for field in ("updated_at", "last_updated_at", "last_activity_at"):
        if meta.get(field):
            updated = _dt(meta.get(field), created)
            break

    mirror = _metadata_mirror(session)
    usage = _session_usage_snapshot(session)
    provider = getattr(agent, "provider", None) or mirror.get("provider") or "unknown"
    model = getattr(agent, "model", None) or mirror.get("model") or "(unknown)"
    project = _project_info_for_cwd(_display_session_cwd(session))
    lines = [
        "Hermes TUI Status",
        "",
        f"Session ID: {key}",
        f"Path: {display_hermes_home()}",
    ]
    if project:
        lines.append(f"Project: {project['name']}")
    title = (meta.get("title") or "").strip()
    if title:
        lines.append(f"Title: {title}")
    lines.extend(
        [
            f"Model: {model} ({provider})",
            f"Created: {created.strftime('%Y-%m-%d %H:%M')}",
            f"Last Activity: {updated.strftime('%Y-%m-%d %H:%M')}",
            f"Tokens: {int(usage.get('total') or 0):,}",
            f"Agent Running: {'Yes' if session.get('running') else 'No'}",
        ]
    )
    return _ok(rid, {"output": "\n".join(lines)})


@method("session.history")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    history = list(session.get("history", []))
    if session.get("session_key"):
        with _session_db(session) as db:
            if db is not None:
                try:
                    history = db.get_messages_as_conversation(
                        session["session_key"],
                        include_ancestors=True,
                        include_inactive=session.get("cxba_binding") is not None,
                        include_row_ids=session.get("cxba_binding") is not None,
                    )
                except Exception as exc:
                    if session.get("cxba_binding") is not None:
                        return _err(
                            rid,
                            5008,
                            f"trusted session history could not be loaded: {exc}",
                        )
                    logger.debug("session.history database lookup failed", exc_info=True)
    return _ok(
        rid,
        {
            "count": len(history),
            "messages": _history_to_messages(
                history, include_raw_indexes=session.get("cxba_binding") is not None
            ),
        },
    )


@method("session.undo")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    # Reject during an in-flight turn.  If we mutated history while
    # the agent thread is running, prompt.submit's post-run history
    # write would either clobber the undo (version matches) or
    # silently drop the agent's output (version mismatch, see below).
    # Neither is what the user wants — make them /interrupt first.
    if session.get("running"):
        return _err(
            rid, 4009, "session busy — /interrupt the current turn before /undo"
        )
    removed = 0
    with session["history_lock"]:
        history = session.get("history", [])
        # Truncate from the last *real* user turn. Popping only trailing
        # assistant/tool then one user left timeline markers
        # (async_delegation_complete, model_switch, …) or compaction
        # handoffs as the undo target — so session.undo removed
        # bookkeeping instead of the last exchange (#80622).
        # Match list_recent_user_messages / CLI turn counting.
        from agent.context_compressor import is_user_originated_turn

        last_user_idx = None
        for i in range(len(history) - 1, -1, -1):
            msg = history[i]
            if is_user_originated_turn(msg):
                last_user_idx = i
                break
        if last_user_idx is not None:
            removed = len(history) - last_user_idx
            del history[last_user_idx:]
            session["history_version"] = int(session.get("history_version", 0)) + 1
    return _ok(rid, {"removed": removed})


@method("session.compress")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    assert session is not None
    if _session_uses_compute_host(session):
        sid = str(params.get("session_id") or "")
        focus_topic = str(params.get("focus_topic", "") or "").strip()
        command = "/compress" + (f" {focus_topic}" if focus_topic else "")
        try:
            ack = _send_compute_host_control(
                sid,
                route_name="session.compress",
                command=command,
                wait=True,
                timeout=120.0,
            )
        except Exception as exc:
            return _err(rid, 5019, f"compute-host compress failed: {exc}")
        if ack.get("type") in {"control.error", "error"}:
            return _err(rid, 4009, str(ack.get("message") or "compute-host compress failed"))
        _apply_compute_host_metadata_mirror(session, ack)
        host_result = ack.get("result")
        if isinstance(host_result, dict):
            # The host owns the isolated session's agent/history, so preserve
            # its structured compression result verbatim. In particular this
            # carries `status: aborted` and `summary.aborted`; flattening the
            # old text-only acknowledgement made Desktop show aborted work as a
            # success toast.
            return _ok(rid, {**host_result, "turn_isolation": True})
        host_info = ack.get("session_info") if isinstance(ack.get("session_info"), dict) else {}
        host_messages = _history_to_messages(ack.get("messages")) if isinstance(ack.get("messages"), list) else []
        # `messages` is returned at top level for the desktop transcript
        # replacement. Keep the host acknowledgement metadata, but do not send
        # the same (potentially large) transcript a second time inside it.
        host_ack = {key: value for key, value in ack.items() if key != "messages"}
        return _ok(
            rid,
            {
                "status": "compressed",
                "turn_isolation": True,
                "host_ack": host_ack,
                "info": host_info,
                "messages": host_messages,
                "usage": host_info.get("usage") if isinstance(host_info.get("usage"), dict) else {},
            },
        )
    session, err = _sess(params, rid)
    if err:
        return err
    if session.get("running"):
        return _err(
            rid, 4009, "session busy — /interrupt the current turn before /compress"
        )
    from agent.conversation_compression import (
        finalize_context_engine_compression_notification,
    )

    sid = params.get("session_id", "")
    focus_topic = str(params.get("focus_topic", "") or "").strip()
    try:
        from agent.manual_compression_feedback import summarize_manual_compression
        from agent.model_metadata import estimate_request_tokens_rough

        with session["history_lock"]:
            before_messages = list(session.get("history", []))
            history_version = int(session.get("history_version", 0))
        before_count = len(before_messages)
        _agent = session["agent"]
        _sys_prompt = getattr(_agent, "_cached_system_prompt", "") or ""
        _tools = getattr(_agent, "tools", None) or None
        before_tokens = (
            estimate_request_tokens_rough(
                before_messages, system_prompt=_sys_prompt, tools=_tools
            )
            if before_count
            else 0
        )

        if before_count >= 4:
            focus_suffix = f', focus: "{focus_topic}"' if focus_topic else ""
            _status_update(
                sid,
                "compressing",
                f"⠋ compressing {before_count} messages "
                f"(~{before_tokens:,} tok){focus_suffix}…",
            )

        try:
            removed, usage = _compress_session_history(
                session,
                focus_topic,
                approx_tokens=before_tokens,
                before_messages=before_messages,
                history_version=history_version,
            )
            with session["history_lock"]:
                messages = list(session.get("history", []))
            after_count = len(messages)
            # Re-read system prompt + tools after compression — _compress_context
            # may have rebuilt the system prompt (_cached_system_prompt=None).
            _sys_prompt_after = (
                getattr(_agent, "_cached_system_prompt", "") or _sys_prompt
            )
            _tools_after = getattr(_agent, "tools", None) or _tools
            after_tokens = (
                estimate_request_tokens_rough(
                    messages,
                    system_prompt=_sys_prompt_after,
                    tools=_tools_after,
                )
                if after_count
                else 0
            )
            agent = session["agent"]
            _sync_session_key_after_compress(sid, session)
            summary = summarize_manual_compression(
                before_messages,
                messages,
                before_tokens,
                after_tokens,
                compression_state=getattr(agent, "context_compressor", None),
            )
            info = _session_info(agent, session)
            _emit("session.info", sid, info)
            finalize_context_engine_compression_notification(
                agent,
                committed=True,
            )
            return _ok(
                rid,
                {
                    "status": "aborted" if summary["aborted"] else "compressed",
                    "removed": removed,
                    "before_messages": before_count,
                    "after_messages": after_count,
                    "before_tokens": before_tokens,
                    "after_tokens": after_tokens,
                    "summary": summary,
                    "usage": usage,
                    "info": info,
                    # Keep this identical to session.resume / session.history:
                    # raw tool results can contain large or sensitive payloads
                    # that belong in persisted history, not the transcript
                    # replacement response.
                    "messages": _history_to_messages(messages),
                },
            )
        finally:
            # Always clear the pinned compressing status so the bar
            # reverts to neutral whether compaction succeeded, was a
            # no-op, or raised.
            _status_update(sid, "ready")
    except CompressionLockHeld as e:
        _status_update(sid, "ready")
        from agent.manual_compression_feedback import (
            describe_compression_lock_skip,
        )
        return _ok(rid, {
            "compressed": False,
            "lock_held": True,
            "message": describe_compression_lock_skip(e.holder),
        })
    except Exception as e:
        finalize_context_engine_compression_notification(
            session["agent"],
            committed=False,
        )
        return _err(rid, 5005, str(e))


@method("session.save")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err

    if _session_uses_compute_host(session):
        sid = str(params.get("session_id") or "")
        try:
            ack = _send_compute_host_control(
                sid,
                route_name="session.save",
                wait=True,
            )
        except Exception as exc:
            return _err(rid, 5011, f"compute-host session save failed: {exc}")
        if ack.get("type") in {"control.error", "error"}:
            return _err(rid, 5011, str(ack.get("message") or "compute-host session save failed"))
        result = ack.get("result")
        if not isinstance(result, dict):
            return _err(rid, 5011, "compute-host session save returned an invalid response")
        return _ok(rid, result)

    agent = session["agent"]
    # Mirror the classic CLI /save: snapshot under the Hermes profile home
    # (~/.hermes/sessions/saved/) rather than the project/workspace CWD, and
    # include the system prompt so the export matches the dashboard save.
    saved_dir = get_hermes_home() / "sessions" / "saved"
    try:
        saved_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return _err(rid, 5011, f"failed to create save directory {saved_dir}: {e}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = saved_dir / f"hermes_conversation_{timestamp}.json"

    with session["history_lock"]:
        messages = list(session.get("history", []))

    session_id = getattr(agent, "session_id", None) or session.get("session_key") or ""
    # Prefer the agent's session_start datetime (matches the classic CLI export);
    # fall back to the gateway session's created_at timestamp.
    agent_start = getattr(agent, "session_start", None)
    if isinstance(agent_start, datetime):
        session_start = agent_start.isoformat()
    else:
        created_at = session.get("created_at")
        session_start = (
            datetime.fromtimestamp(created_at).isoformat()
            if isinstance(created_at, (int, float))
            else ""
        )

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "model": getattr(agent, "model", ""),
                    "session_id": session_id,
                    "session_start": session_start,
                    "system_prompt": getattr(agent, "_cached_system_prompt", "") or "",
                    "messages": messages,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        return _ok(rid, {"file": str(path)})
    except Exception as e:
        return _err(rid, 5011, str(e))


@method("session.close")
def _(rid, params: dict) -> dict:
    sid = params.get("session_id", "")
    # Serialize only the ownership claim against session.resume / the orphan
    # reaper. Finalization may run arbitrary plugin/agent cleanup and must not
    # keep every unrelated session.resume waiting behind it.
    with _session_resume_lock:
        session = _pop_session_by_id(sid)
    closed = _teardown_popped_session(session, end_reason="tui_close")
    return _ok(rid, {"closed": closed})


@method("session.branch")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    parent_cxba_binding = session.get("cxba_binding")
    branch_cxba_binding = None
    if parent_cxba_binding is not None:
        from tui_gateway.transport import has_cxba_private_authority

        if not has_cxba_private_authority():
            return _err(rid, 4035, "CXBA session branch requires a trusted private connection")
        if "cxba_context" not in params:
            return _err(rid, 4034, "CXBA session branch requires a new cxba_context")
        try:
            from tui_gateway.cxba_runtime import validate_session_binding

            branch_cxba_binding = validate_session_binding(params.get("cxba_context"))
        except ValueError as exc:
            return _err(rid, 4034, f"invalid trusted cxba_context: {exc}")
        if branch_cxba_binding["case_id"] != parent_cxba_binding["case_id"]:
            return _err(rid, 4036, "branch cxba_context must remain in the parent Case")
        if (
            branch_cxba_binding["business_session_id"]
            != parent_cxba_binding["business_session_id"]
        ):
            return _err(rid, 4036, "branch must remain in the parent business Session")
        if (
            branch_cxba_binding["business_branch_id"]
            == parent_cxba_binding["business_branch_id"]
        ):
            return _err(rid, 4036, "branch requires a new business_branch_id")
        for live_session in _sessions.values():
            live_binding = live_session.get("cxba_binding")
            if (
                isinstance(live_binding, dict)
                and live_binding.get("business_session_id")
                == branch_cxba_binding["business_session_id"]
                and live_binding.get("business_branch_id")
                == branch_cxba_binding["business_branch_id"]
            ):
                return _err(rid, 4092, "business branch is already bound")
    elif "cxba_context" in params:
        return _err(rid, 4036, "a normal Hermes branch cannot acquire a CXBA binding")
    # Branch must write into the parent's profile-scoped state.db (app-global
    # remote mode). Using the launch handle would orphan branch rows + history.
    persisted_branch_history = None
    with _session_db(session) as db:
        if db is None:
            return _db_unavailable_error(rid, code=5008)
        old_key = session["session_key"]
        with session["history_lock"]:
            history = [dict(msg) for msg in session.get("history", [])]
        if not history:
            return _err(rid, 4008, "nothing to branch — send a message first")
        target_message_index = params.get("target_message_index")
        target_message_row_id = params.get("target_message_row_id")
        if target_message_index is not None and target_message_row_id is not None:
            return _err(rid, 4008, "provide only target_message_row_id")
        if target_message_row_id is not None:
            if not isinstance(target_message_row_id, int) or target_message_row_id <= 0:
                return _err(rid, 4008, "target_message_row_id must be a positive integer")
            history = db.get_messages_as_conversation(
                old_key,
                include_ancestors=True,
                include_inactive=True,
                include_row_ids=True,
            )
            target_message_index = next(
                (
                    index
                    for index, message in enumerate(history)
                    if message.get("_row_id") == target_message_row_id
                ),
                None,
            )
            if target_message_index is None:
                return _err(rid, 4008, "target_message_row_id is not in the session lineage")
        if target_message_index is not None:
            if (
                not isinstance(target_message_index, int)
                or target_message_index < 0
                or target_message_index >= len(history)
            ):
                return _err(rid, 4008, "target_message_index is outside the session history")
            if "edited_content" not in params or not isinstance(params.get("edited_content"), str):
                return _err(rid, 4008, "edited_content must be a string")
            edited = dict(history[target_message_index])
            edited["content"] = params["edited_content"]
            edited.pop("api_content", None)
            history = history[:target_message_index] + [edited]
        else:
            count = params.get("count")
            if isinstance(count, int) and count > 0:
                history = history[:count]
        new_key = _new_session_key()
        new_sid = uuid.uuid4().hex[:8]
        source = _session_source(session)
        lease = None  # claimed lazily on the first turn (_ensure_active_session_slot)
        branch_name = params.get("name", "")
        try:
            if branch_name:
                title = branch_name
            else:
                current = db.get_session_title(old_key) or "branch"
                title = (
                    db.get_next_title_in_lineage(current)
                    if hasattr(db, "get_next_title_in_lineage")
                    else f"{current} (branch)"
                )
            branch_model_config = {"_branched_from": old_key}
            if branch_cxba_binding is not None:
                from tui_gateway.cxba_runtime import binding_from_model_config

                if not hasattr(db, "list_sessions_rich"):
                    return _err(rid, 5008, "session store cannot verify business branch binding")
                for stored in db.list_sessions_rich(
                    limit=10_000, include_children=True, include_archived=True
                ):
                    existing_binding = binding_from_model_config(stored.get("model_config"))
                    if (
                        existing_binding is not None
                        and existing_binding["business_session_id"]
                        == branch_cxba_binding["business_session_id"]
                        and existing_binding["business_branch_id"]
                        == branch_cxba_binding["business_branch_id"]
                    ):
                        return _err(rid, 4092, "business branch is already bound")
                branch_model_config["_cxba_binding"] = copy.deepcopy(branch_cxba_binding)
            db.create_session(
                new_key,
                source=source,
                model=_resolve_model(),
                # Stable _branched_from marker so list_sessions_rich() keeps the
                # branch visible in /resume and /sessions. The TUI branch leaves
                # the parent live (no end_reason='branched'), so the legacy
                # end_reason heuristic never matches it — the marker is the only
                # thing that surfaces TUI branches. See issue #20856.
                model_config=branch_model_config,
                parent_session_id=old_key,
                cwd=_session_cwd(session),
                # The branch stays on its parent's profile. Explicit stamp (not
                # just the parent-backfill) so it holds even when the parent row
                # predates the profile_name column.
                profile_name=(
                    Path(session["profile_home"]).name
                    if session.get("profile_home")
                    else None
                ),
            )
            # Copy the whole parent history in bounded-chunk transactions —
            # a branch seed can be hundreds of rows, and per-row transactions
            # were the write-amplification pattern removed in #23254.
            db.append_messages_batch(
                new_key,
                [
                    {
                        **{
                            key: copy.deepcopy(value)
                            for key, value in msg.items()
                            if not str(key).startswith("_")
                        },
                        # Preserve the parent's original message timestamps —
                        # branch copies are history, not new activity (9d73006ad).
                        "timestamp": msg.get("timestamp"),
                    }
                    for msg in history
                ],
                chunk_rows=500,
            )
            db.set_session_title(new_key, title)
            if branch_cxba_binding is not None:
                persisted_branch_history = db.get_messages_as_conversation(
                    new_key,
                    include_row_ids=True,
                )
        except Exception as e:
            if lease is not None:
                lease.release()
            return _err(rid, 5008, f"branch failed: {e}")
    # Bound before the try so the ownership finally below can never see them
    # unbound, whatever raises inside.
    branch_db = None
    branch_owns_db = False
    try:
        # Bind the branched AGENT to the parent's profile, mirroring
        # session.create/resume: home override so config/skills/memory resolve
        # to the profile during the build, and the profile's own state.db
        # handle so the live agent's message flushes — and any later
        # compression rotation — persist there. Writing only the row to the
        # parent's db while the agent stayed on the launch handle would
        # recreate the cross-profile split one turn later.
        parent_home = session.get("profile_home")
        if parent_home:
            from hermes_state import SessionDB

            # DEDICATED handle, same ownership rule as session.resume: ours
            # until the branched agent takes it below. _make_agent raising, or
            # _init_session raising, both leave here without that transfer.
            branch_db = SessionDB(db_path=Path(parent_home) / "state.db")
            branch_owns_db = True
        home_token = (
            set_hermes_home_override(parent_home) if parent_home else None
        )
        # The home override alone only moves config/skills/memory; credentials
        # resolve through get_secret(), which without a scope falls through to
        # process os.environ — the LAUNCH profile's .env. Install the parent's
        # secret scope for the build, exactly as session.create/resume do
        # (#67605), so the branched agent authenticates as its own profile.
        secret_token = (
            set_secret_scope(build_profile_secret_scope(Path(parent_home)))
            if parent_home
            else None
        )
        try:
            tokens = _set_session_context(new_key)
            try:
                agent = _make_agent(
                    new_sid,
                    new_key,
                    session_id=new_key,
                    session_db=branch_db,
                    platform_override=source,
                )
            finally:
                _clear_session_context(tokens)
            _init_session(
                new_sid,
                new_key,
                agent,
                list(persisted_branch_history or history),
                cols=session.get("cols", 80),
                cwd=_session_cwd(session),
                session_db=branch_db,
                source=source,
                profile_home=parent_home,
            )
            if new_sid in _sessions:
                _sessions[new_sid]["cxba_binding"] = branch_cxba_binding
            # Ownership TRANSFER — the branched session's agent holds this
            # handle for its whole life and closes it on teardown. Drop is
            # unconditional for the same reason as session.resume: past
            # _init_session the branched session is registered against this
            # handle, so the finally must not close it.
            _transfer_db_to_agent(agent, branch_db)
            branch_owns_db = False
        finally:
            if secret_token is not None:
                reset_secret_scope(secret_token)
            if home_token is not None:
                reset_hermes_home_override(home_token)
        if new_sid in _sessions:
            _sessions[new_sid]["active_session_lease"] = lease
    except Exception as e:
        if lease is not None:
            lease.release()
        with _session_resume_lock:
            partial = _pop_session_by_id(new_sid)
        if partial is not None:
            _teardown_popped_session(partial, end_reason="branch_init_failed")
        try:
            with _session_db(session) as cleanup_db:
                if cleanup_db is not None:
                    sessions_dir = (
                        Path(parent_home) / "sessions"
                        if parent_home
                        else get_hermes_home() / "sessions"
                    )
                    cleanup_db.delete_session(new_key, sessions_dir=sessions_dir)
        except Exception:
            logger.error(
                "event=session_branch stage=compensate status=failed storedSessionId=%s",
                new_key,
                exc_info=True,
            )
        return _err(rid, 5000, f"agent init failed on branch: {e}")
    finally:
        if branch_owns_db and branch_db is not None:
            with contextlib.suppress(Exception):
                branch_db.close()
    branched_session = _sessions.get(new_sid)
    response_history = persisted_branch_history or history
    edited_message_row_id = None
    if target_message_index is not None and target_message_index < len(response_history):
        edited_message_row_id = response_history[target_message_index].get("_row_id")
    return _ok(
        rid,
        {
            "session_id": new_sid,
            "stored_session_id": new_key,
            "title": title,
            "parent": old_key,
            "message_count": len(response_history),
            "messages": _history_to_messages(
                response_history,
                include_raw_indexes=branch_cxba_binding is not None,
            ),
            **(
                {
                    "edited_message_row_id": edited_message_row_id,
                    "edited_raw_message_index": target_message_index,
                }
                if edited_message_row_id is not None
                else {}
            ),
            "info": _session_info(agent, branched_session),
        },
    )


def _require_cxba_private(rid):
    from tui_gateway.transport import has_cxba_private_authority

    if not has_cxba_private_authority():
        return _err(rid, 4039, "operation requires a trusted CXBA private connection")
    return None


@method("cxba.business_session.retire")
def _(rid, params: dict) -> dict:
    """Retire one CXBA business Session without deleting its transcript."""
    if err := _require_cxba_private(rid):
        return err
    case_id = str(params.get("case_id") or "").strip()
    business_session_id = str(params.get("business_session_id") or "").strip()
    reason = str(params.get("reason") or "").strip().upper()
    if not case_id or not business_session_id:
        return _err(rid, 4002, "case_id and business_session_id are required")
    if reason not in {"CASE_CLOSED", "ARCHIVED_SESSION_DELETED"}:
        return _err(
            rid,
            4002,
            "reason must be CASE_CLOSED or ARCHIVED_SESSION_DELETED",
        )

    from tui_gateway.cxba_runtime import (
        binding_from_model_config,
        retire_business_session_runs,
        validate_business_session_runs_retirable,
    )

    try:
        validate_business_session_runs_retirable(case_id, business_session_id)
    except ValueError as exc:
        return _err(rid, 4093, str(exc))

    profile = str(params.get("profile") or "").strip() or None
    home = _profile_home(profile) or get_hermes_home()
    live_sessions = []
    stable_session_ids = []
    newly_retired = []
    with _session_resume_lock:
        with _profile_db(params) as db:
            if db is None:
                return _db_unavailable_error(rid, code=5000)
            rows = db.list_sessions_rich(
                limit=1_000_000,
                include_children=True,
                include_archived=True,
                project_compression_tips=False,
            )
            matching_rows = []
            for row in rows:
                binding = binding_from_model_config(row.get("model_config"))
                if (
                    binding is not None
                    and binding["case_id"] == case_id
                    and binding["business_session_id"] == business_session_id
                ):
                    matching_rows.append(row)

            with _sessions_lock:
                matching_live = [
                    (sid, session)
                    for sid, session in _sessions.items()
                    if isinstance(session.get("cxba_binding"), dict)
                    and session["cxba_binding"].get("case_id") == case_id
                    and session["cxba_binding"].get("business_session_id")
                    == business_session_id
                ]
            claimed_live = []
            for _sid, session in matching_live:
                with session["history_lock"]:
                    if session.get("running"):
                        for claimed in claimed_live:
                            with claimed["history_lock"]:
                                claimed.pop("_cxba_retiring", None)
                        return _err(
                            rid,
                            4093,
                            "business Session still has a running Hermes Session",
                        )
                    session["_cxba_retiring"] = True
                    claimed_live.append(session)
            with _sessions_lock:
                for sid, session in matching_live:
                    if _sessions.get(sid) is not session:
                        continue
                    popped = _sessions.pop(sid)
                    popped["_sid"] = sid
                    live_sessions.append(popped)

            for row in matching_rows:
                stable_session_id = str(row.get("id") or "").strip()
                if not stable_session_id:
                    continue
                stable_session_ids.append(stable_session_id)
                if db.get_session_model_config_value(
                    stable_session_id, "_cxba_retired", False
                ) is not True:
                    db.patch_session_model_config(
                        stable_session_id, {"_cxba_retired": True}
                    )
                    newly_retired.append(stable_session_id)

    for session in live_sessions:
        _teardown_popped_session(session, end_reason="cxba_retired")

    from tui_gateway.turn_marker import clear_turn_marker

    for stable_session_id in stable_session_ids:
        clear_turn_marker(home, stable_session_id)
    try:
        retired_run_ids = retire_business_session_runs(
            case_id, business_session_id
        )
    except ValueError as exc:
        return _err(rid, 4093, str(exc))

    return _ok(
        rid,
        {
            "status": "retired",
            "case_id": case_id,
            "business_session_id": business_session_id,
            "stable_session_ids": stable_session_ids,
            "retired_run_ids": retired_run_ids,
            "already_retired": bool(stable_session_ids) and not newly_retired,
            "messages_preserved": True,
        },
    )


@method("run.events.fetch")
def _(rid, params: dict) -> dict:
    if err := _require_cxba_private(rid):
        return err
    run_id = str(params.get("run_id") or "").strip()
    case_id = str(params.get("case_id") or "").strip()
    if not run_id:
        return _err(rid, 4002, "run_id is required")
    if not case_id:
        return _err(rid, 4002, "case_id is required")
    after_event_id = str(params.get("after_event_id") or "").strip()
    try:
        from tui_gateway.cxba_runtime import fetch_events

        events = fetch_events(run_id, after_event_id, case_id=case_id)
    except KeyError:
        return _err(rid, 4040, "Run event buffer not found")
    except ValueError as exc:
        return _err(rid, 4002, str(exc))
    return _ok(rid, {"run_id": run_id, "events": events})


@method("run.events.purge")
def _(rid, params: dict) -> dict:
    if err := _require_cxba_private(rid):
        return err
    case_id = str(params.get("case_id") or "").strip()
    run_id = str(params.get("run_id") or "").strip()
    if not case_id or not run_id:
        return _err(rid, 4002, "case_id and run_id are required")
    try:
        from tui_gateway.cxba_runtime import purge_run_events

        purge_run_events(case_id, run_id)
    except ValueError as exc:
        return _err(rid, 4002, str(exc))
    return _ok(rid, {"run_id": run_id, "purged": True})


@method("run.heartbeat")
def _(rid, params: dict) -> dict:
    if err := _require_cxba_private(rid):
        return err
    run_id = str(params.get("run_id") or "").strip()
    if not run_id:
        return _err(rid, 4002, "run_id is required")
    try:
        from tui_gateway.cxba_runtime import get_run, poll_run_heartbeat

        run = get_run(run_id)
        if run is None:
            return _err(rid, 4040, "Run not found")
        levels, changed, event_type = poll_run_heartbeat(run_id)
        healthy = not bool(get_run(run_id).heartbeat_lost)
        if changed and event_type:
            _emit(event_type, run.runtime_session_id, {"levels": levels})
        return _ok(
            rid,
            {"run_id": run_id, "status": "RUNNING" if healthy else "FAILED", **levels},
        )
    except (OSError, ValueError) as exc:
        return _err(rid, 5000, f"heartbeat probe failed: {exc}")


@method("run.approval.resolve")
def _(rid, params: dict) -> dict:
    if err := _require_cxba_private(rid):
        return err
    sid = str(params.get("session_id") or "").strip()
    run_id = str(params.get("run_id") or "").strip()
    proposal_id = str(params.get("proposal_id") or "").strip()
    status = str(params.get("status") or "").strip()
    try:
        pending_count = int(params.get("pending_count"))
    except (TypeError, ValueError):
        return _err(rid, 4002, "pending_count must be an integer")
    session = _sessions.get(sid)
    if session is None:
        return _err(rid, 4004, "session not found")
    recovered_continuation = "run_context" in params or "case_context" in params
    if recovered_continuation and (
        "run_context" not in params or "case_context" not in params
    ):
        return _err(
            rid,
            4039,
            "recovered approval continuation requires run_context and case_context",
        )
    continuation_created = False
    try:
        from tui_gateway.cxba_runtime import (
            approval_prompt,
            approval_events_snapshot,
            attach_run,
            detach_completed_run,
            discard_queued_approval_result,
            get_run,
            queue_approval_result,
            run_for_session,
            validate_case_context,
            validate_case_context_matches_binding,
            validate_run_matches_binding,
        )

        if recovered_continuation:
            with session["history_lock"]:
                if session.get("running"):
                    return _err(
                        rid,
                        4091,
                        "recovered Session is busy; retry approval delivery explicitly",
                    )
                if session.get("agent") is None:
                    return _err(
                        rid,
                        5032,
                        "recovered Session agent is not ready; retry approval delivery explicitly",
                    )
            try:
                from tools.run_sandbox import validate_run_context

                trusted_run_context = validate_run_context(params.get("run_context"))
                trusted_case_context = validate_case_context(params.get("case_context"))
                validate_run_matches_binding(
                    session.get("cxba_binding"), trusted_run_context
                )
                validate_case_context_matches_binding(
                    session.get("cxba_binding"), trusted_case_context
                )
            except (OSError, ValueError) as exc:
                return _err(rid, 4038, f"invalid recovered approval context: {exc}")
            if trusted_run_context.run_id != run_id:
                return _err(rid, 4038, "run_context run_id does not match run_id")
            active_run = run_for_session(sid)
            if active_run is not None and active_run.run_id != run_id:
                return _err(
                    rid,
                    4093,
                    "runtime session already has a different active Run",
                )
            run, continuation_created = attach_run(
                run_context=trusted_run_context,
                stored_session_id=str(session.get("session_key") or ""),
                runtime_session_id=sid,
                case_context=trusted_case_context,
            )
            session["active_cxba_run_id"] = run_id
        else:
            run = get_run(run_id)
            if run is None or run.runtime_session_id != sid:
                return _err(rid, 4040, "Run does not belong to the runtime session")
        event = queue_approval_result(
            run_id,
            proposal_id=proposal_id,
            status=status,
            content=params.get("content"),
            pending_count=pending_count,
            source_run_id=str(params.get("source_run_id") or "").strip() or None,
        )
        _emit("approval.result", sid, event)
        if pending_count > 0:
            _emit(
                "approval.result.queued",
                sid,
                {"proposal_id": proposal_id, "pending_count": pending_count},
            )
            return _ok(
                rid,
                {
                    "status": "queued",
                    "run_id": run_id,
                    "pending_count": pending_count,
                },
            )

        prompts = [approval_prompt(item) for item in approval_events_snapshot(run_id)]
        prompt = "\n\n".join(prompts)
        with session["history_lock"]:
            running = bool(session.get("running"))
            agent = session.get("agent")
            if not running:
                session["running"] = True
        if running:
            if agent is None or not hasattr(agent, "steer") or not agent.steer(prompt):
                discard_queued_approval_result(run_id, event)
                if continuation_created:
                    detach_completed_run(run_id, "FAILED")
                return _err(rid, 4091, "active Run could not accept the approval result")
            session["_cxba_approval_steer_pending"] = True
            _emit("approval.result.queued", sid, {"proposal_id": proposal_id})
            return _ok(rid, {"status": "queued", "run_id": run_id})

        started = _run_prompt_submit(
            rid,
            sid,
            session,
            prompt,
            display_kind="cxba_approval_result",
            display_metadata={"proposal_id": proposal_id, "status": event["status"]},
            trusted_run_context=run.context,
            trusted_case_context=run.case_context,
            consumes_cxba_approvals=True,
        )
        if not started:
            discard_queued_approval_result(run_id, event)
            if continuation_created:
                detach_completed_run(run_id, "FAILED")
            with session["history_lock"]:
                session["running"] = False
            return _err(
                rid,
                5031,
                "approval continuation could not start; retry delivery explicitly",
            )
        return _ok(rid, {"status": "continuing", "run_id": run_id})
    except (KeyError, ValueError) as exc:
        if continuation_created:
            detach_completed_run(run_id, "FAILED")
        with session["history_lock"]:
            if not session.get("_run_thread"):
                session["running"] = False
        return _err(rid, 4002, str(exc))


@method("session.safe_stop")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    if _session_uses_compute_host(session):
        return _err(rid, 5020, "safe stop is unavailable for compute-host turns")
    sid = str(params.get("session_id") or "")
    with session["history_lock"]:
        running = bool(session.get("running"))
        if not running and _is_cxba_run_session(sid):
            from tui_gateway.cxba_runtime import request_safe_stop, run_for_session

            run = run_for_session(sid)
            if run is not None and run.pending_proposals:
                request_safe_stop(run.run_id)
                session["_safe_stop_requested"] = True
                _emit("safe_stop.requested", sid, {"status": "SAFE_STOPPING"})
                return _ok(
                    rid,
                    {
                        "status": "safe_stopping",
                        "control_outcome": "accepted",
                        "agent_ready": False,
                    },
                )
        if not running:
            return _ok(rid, {"status": "idle", "control_outcome": "idle"})
        session["_safe_stop_requested"] = True
        session["queued_prompt"] = None
        session.pop("queued_prompts", None)
        session["_queued_prompt_generation"] = int(
            session.get("_queued_prompt_generation", 0)
        ) + 1
        agent = session.get("agent")
    accepted = bool(
        agent is not None
        and getattr(agent, "request_safe_stop", lambda: False)()
    )
    if _is_cxba_run_session(sid):
        from tui_gateway.cxba_runtime import request_safe_stop, run_for_session

        run = run_for_session(sid)
        if run is not None:
            request_safe_stop(run.run_id)
        _emit("safe_stop.requested", sid, {"status": "SAFE_STOPPING"})
    return _ok(
        rid,
        {
            "status": "safe_stopping",
            "control_outcome": "accepted",
            "agent_ready": accepted,
        },
    )


@method("session.interrupt")
def _(rid, params: dict) -> dict:
    # Keypress barge-in: stopping the turn also silences its streaming TTS
    # (voice is process-global, so no per-session scoping is needed).
    _tts_stream_stop()
    session, err = _sess_nowait(params, rid)
    if err:
        run_id = str(params.get("run_id") or "").strip()
        if run_id and _require_cxba_private(rid) is None:
            try:
                from tools.run_sandbox import destroy_run_sandbox

                destroy_run_sandbox(run_id)
            except (OSError, RuntimeError, ValueError) as exc:
                return _err(rid, 5032, f"Run Sandbox cleanup failed: {exc}")
            return _ok(
                rid,
                {
                    "status": "interrupted",
                    "control_outcome": "idle",
                    "turn_isolation": True,
                    "runtime_session_present": False,
                },
            )
        return err
    if _session_uses_compute_host(session):
        sid = str(params.get("session_id") or "")
        compute_running = bool(session.get("running"))
        if compute_running:
            try:
                _get_compute_host_supervisor().interrupt(sid, request_id=f"interrupt-{rid}")
            except Exception as exc:
                return _err(rid, 5019, f"compute-host interrupt failed: {exc}")
        with session["history_lock"]:
            session["_turn_cancel_requested"] = True
            session["queued_prompt"] = None
            session.pop("queued_prompts", None)
            session["_queued_prompt_generation"] = int(session.get("_queued_prompt_generation", 0)) + 1
        _clear_pending(sid)
        try:
            from tools.approval import resolve_gateway_approval

            resolve_gateway_approval(session["session_key"], "deny", resolve_all=True)
        except Exception:
            pass
        return _ok(
            rid,
            {
                "status": "interrupted",
                "control_outcome": "accepted" if compute_running else "idle",
                "turn_isolation": True,
            },
        )
    session, err = _sess(params, rid)
    if err:
        return err
    # Safety net: if the turn's run thread is already gone but `running` stayed
    # stuck (a crash/desync that skipped the run loop's `finally`), force-clear it
    # so the session can't be permanently bricked at 4009 "session busy" — every
    # send/restore/resume would otherwise reject until a full backend restart.
    # Always tell the agent to interrupt when the session claims a run is active:
    # stale flags are cleared below, and fresh turns clear the interrupt flag at
    # entry. This keeps a stale/missing thread handle from making Stop a no-op.
    run_thread = session.get("_run_thread")
    run_thread_alive = run_thread is not None and run_thread.is_alive()
    should_interrupt = bool(session.get("running"))
    with session["history_lock"]:
        session["_turn_cancel_requested"] = True
        session["queued_prompt"] = None
        session.pop("queued_prompts", None)
        session["_queued_prompt_generation"] = int(session.get("_queued_prompt_generation", 0)) + 1
    if should_interrupt:
        from agent.interrupt_compat import request_hard_interrupt

        request_hard_interrupt(session["agent"])
    if not run_thread_alive:
        with session["history_lock"]:
            if session.get("running"):
                session["running"] = False
                _clear_inflight_turn(session)

    # Stop = stop the TURN (cooperative interrupt above also kills the in-flight
    # foreground subprocess). Background processes the agent started (dev servers,
    # watchers) are intentionally left running — kill those individually with the
    # "x" on the task row (process.kill). Don't reap them here.
    # Scope the pending-prompt release to THIS session.  A global
    # _clear_pending() would collaterally cancel clarify/sudo/secret
    # prompts on unrelated sessions sharing the same tui_gateway
    # process, silently resolving them to empty strings.
    _clear_pending(params.get("session_id", ""))
    try:
        from tools.approval import resolve_gateway_approval

        resolve_gateway_approval(session["session_key"], "deny", resolve_all=True)
    except Exception:
        pass
    sid = str(params.get("session_id") or "")
    cxba_force_stopped = False
    if _is_cxba_run_session(sid):
        try:
            from tools.run_sandbox import destroy_run_sandbox
            from tui_gateway.cxba_runtime import (
                detach_completed_run,
                force_stop_run,
                run_for_session,
            )

            run = run_for_session(sid)
            if run is not None:
                cxba_force_stopped = True
                force_stop_run(run.run_id)
                _emit("force_stop.requested", sid, {"status": "FORCE_STOPPING"})
                from tools.async_delegation import interrupt_for_session

                interrupt_for_session(
                    session_key=str(session.get("session_key") or ""),
                    origin_ui_session_id=sid,
                    parent_session_id=str(
                        getattr(session.get("agent"), "session_id", "") or ""
                    ),
                    reason="cxba_force_stop",
                )
                destroy_run_sandbox(run.run_id)
                _emit("force_stop.completed", sid, {"status": "FORCE_STOPPED"})
                detach_completed_run(run.run_id, "FORCE_STOPPED")
                session.pop("active_cxba_run_id", None)
        except Exception as exc:
            logger.exception("event=force_stop stage=destroy status=failed sessionId=%s", sid)
            return _err(rid, 5032, f"force stop could not remove the Run sandbox: {exc}")
    return _ok(
        rid,
        {
            "status": "interrupted",
            "control_outcome": (
                "accepted" if should_interrupt or cxba_force_stopped else "idle"
            ),
        },
    )


@method("delegation.status")
def _(rid, params: dict) -> dict:
    from tools.delegate_tool import (
        is_spawn_paused,
        list_active_subagents,
        _get_max_concurrent_children,
        _get_max_spawn_depth,
    )

    return _ok(
        rid,
        {
            "active": list_active_subagents(),
            "paused": is_spawn_paused(),
            "max_spawn_depth": _get_max_spawn_depth(),
            "max_concurrent_children": _get_max_concurrent_children(),
        },
    )


@method("delegation.pause")
def _(rid, params: dict) -> dict:
    from tools.delegate_tool import set_spawn_paused

    paused = bool(params.get("paused", True))
    return _ok(rid, {"paused": set_spawn_paused(paused)})


@method("subagent.interrupt")
def _(rid, params: dict) -> dict:
    from tools.delegate_tool import interrupt_subagent

    subagent_id = str(params.get("subagent_id") or "").strip()
    if not subagent_id:
        return _err(rid, 4000, "subagent_id required")
    ok = interrupt_subagent(subagent_id)
    return _ok(rid, {"found": ok, "subagent_id": subagent_id})


@method("subagent.steer")
def _(rid, params: dict) -> dict:
    """Queue steering text into a live delegated child without stopping it.

    The redirection-side mirror of subagent.interrupt: resolves the child in
    the delegation registry and calls AIAgent.steer(), which appends the text
    to the child's last tool result at its next iteration boundary — the
    in-flight tool call is never cut. "queued" is not "delivered": a child
    already past its final tool batch has no boundary left to drain into,
    and that race surfaces as ``missed_steer`` on the parent's completion
    entry instead of being silently dropped.
    """
    from tools.delegate_tool import steer_subagent

    subagent_id = str(params.get("subagent_id") or "").strip()
    if not subagent_id:
        return _err(rid, 4000, "subagent_id required")
    text = (params.get("text") or "").strip()
    if not text:
        return _err(rid, 4002, "text is required")
    _invoking_session, err = _sess_nowait(params, rid)
    if err:
        return err
    invoking_session_id = str(params.get("session_id") or "").strip()
    invoking_transport, invoking_session = _current_session_steer_authority(
        invoking_session_id
    )
    queued = False
    if invoking_transport is not None and invoking_session is not None:
        queued = steer_subagent(
            subagent_id,
            text,
            owner_session_id=invoking_session_id,
            owner_transport=invoking_transport,
            owner_session_record=invoking_session,
        )
    return _ok(
        rid,
        {
            "status": "queued" if queued else "rejected",
            "subagent_id": subagent_id,
            "text": text,
        },
    )


@method("spawn_tree.save")
def _(rid, params: dict) -> dict:
    session_id = str(params.get("session_id") or "").strip()
    subagents = params.get("subagents") or []
    if not isinstance(subagents, list) or not subagents:
        return _err(rid, 4000, "subagents list required")

    from datetime import datetime

    started_at = params.get("started_at")
    finished_at = params.get("finished_at") or time.time()
    label = str(params.get("label") or "")
    ts = datetime.utcfromtimestamp(float(finished_at)).strftime("%Y%m%dT%H%M%S")
    fname = f"{ts}.json"
    d = _spawn_tree_session_dir(session_id or "default")
    path = d / fname
    try:
        payload = {
            "session_id": session_id,
            "started_at": float(started_at) if started_at else None,
            "finished_at": float(finished_at),
            "label": label,
            "subagents": subagents,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return _err(rid, 5000, f"spawn_tree.save failed: {exc}")

    _append_spawn_tree_index(
        d,
        {
            "path": str(path),
            "session_id": session_id,
            "started_at": payload["started_at"],
            "finished_at": payload["finished_at"],
            "label": label,
            "count": len(subagents),
        },
    )

    return _ok(rid, {"path": str(path), "session_id": session_id})


@method("spawn_tree.list")
def _(rid, params: dict) -> dict:
    session_id = str(params.get("session_id") or "").strip()
    limit = int(params.get("limit") or 50)
    cross_session = bool(params.get("cross_session"))

    if cross_session:
        root = _spawn_trees_root()
        roots = [p for p in root.iterdir() if p.is_dir()]
    else:
        roots = [_spawn_tree_session_dir(session_id or "default")]

    entries: list[dict] = []
    for d in roots:
        indexed = _read_spawn_tree_index(d)
        if indexed:
            # Skip index entries whose snapshot file was manually deleted.
            entries.extend(
                e for e in indexed if (p := e.get("path")) and Path(p).exists()
            )
            continue

        # Fallback for legacy (pre-index) sessions: full scan.  O(N) reads
        # but only runs once per session until the next save writes the index.
        for p in d.glob("*.json"):
            if p.name == _SPAWN_TREE_INDEX:
                continue
            try:
                stat = p.stat()
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    raw = {}
                subagents = raw.get("subagents") or []
                entries.append(
                    {
                        "path": str(p),
                        "session_id": raw.get("session_id") or d.name,
                        "finished_at": raw.get("finished_at") or stat.st_mtime,
                        "started_at": raw.get("started_at"),
                        "label": raw.get("label") or "",
                        "count": len(subagents) if isinstance(subagents, list) else 0,
                    }
                )
            except OSError:
                continue

    entries.sort(key=lambda e: e.get("finished_at") or 0, reverse=True)
    return _ok(rid, {"entries": entries[:limit]})


@method("spawn_tree.load")
def _(rid, params: dict) -> dict:
    from pathlib import Path

    raw_path = str(params.get("path") or "").strip()
    if not raw_path:
        return _err(rid, 4000, "path required")

    # Reject paths escaping the spawn-trees root.
    root = _spawn_trees_root().resolve()
    try:
        resolved = Path(raw_path).resolve()
        resolved.relative_to(root)
    except (ValueError, OSError) as exc:
        return _err(rid, 4030, f"path outside spawn-trees root: {exc}")

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _err(rid, 5000, f"spawn_tree.load failed: {exc}")

    return _ok(rid, payload)


@method("session.steer")
def _(rid, params: dict) -> dict:
    """Inject a user message into the next tool result without interrupting.

    Mirrors AIAgent.steer(). Safe to call while a turn is running — the text
    lands on the last tool result of the next tool batch and the model sees
    it on its next iteration. No interrupt, no new user turn, no role
    alternation violation.
    """
    text = (params.get("text") or "").strip()
    if not text:
        return _err(rid, 4002, "text is required")
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    sid = str(params.get("session_id") or "")
    cxba_run = None
    model_text = text
    if _is_cxba_run_session(sid):
        from tui_gateway.cxba_runtime import (
            case_context_model_prompt,
            run_for_session,
            update_run_case_context,
        )

        cxba_run = run_for_session(sid)
        if cxba_run is None:
            return _err(rid, 4094, "CXBA Run is terminal or detached")
        if cxba_run.status == "SAFE_STOPPING":
            return _err(rid, 4094, "CXBA Run is stopping and cannot accept steer")
        if "case_context" not in params:
            return _err(rid, 4039, "CXBA Run control requires a trusted case_context")
        try:
            cxba_run = update_run_case_context(
                sid, session.get("cxba_binding"), params.get("case_context")
            )
        except ValueError as exc:
            return _err(rid, 4039, f"invalid trusted case_context: {exc}")
        if current_case_note := case_context_model_prompt(cxba_run.case_context):
            model_text = f"{current_case_note}\n\n{text}"
        if not session.get("running"):
            with session["history_lock"]:
                if session.get("running"):
                    return _err(rid, 4009, "session busy")
                session["running"] = True
            if not _run_prompt_submit(
                rid,
                sid,
                session,
                text,
                display_kind="cxba_read_only_continuation",
                trusted_run_context=cxba_run.context,
                trusted_case_context=cxba_run.case_context,
            ):
                with session["history_lock"]:
                    session["running"] = False
                return _err(rid, 5031, "CXBA Run continuation could not start")
            _emit("steer.received", sid, {"status": "CONTINUING"})
            return _ok(
                rid,
                {
                    "status": "continuing",
                    "control_outcome": "accepted",
                    "text": text,
                },
            )
    agent = session.get("agent")
    if agent is None or not hasattr(agent, "steer"):
        return _err(rid, 4010, "agent does not support steer")
    try:
        accepted = agent.steer(model_text)
    except Exception as exc:
        return _err(rid, 5000, f"steer failed: {exc}")
    if accepted:
        # Record the correction on the live turn exactly like session.redirect
        # does. Without this, a resume/reconnect while the turn is running
        # rebuilds the transcript from the inflight snapshot and the steered
        # text has no user bubble — the "my message vanished on reload" loss.
        with session["history_lock"]:
            _record_inflight_correction(session, text)
            session["last_active"] = time.time()
        if cxba_run is not None:
            _emit(
                "steer.received",
                str(params.get("session_id") or ""),
                {"status": "QUEUED"},
            )
    return _ok(
        rid,
        {
            "status": "queued" if accepted else "rejected",
            "control_outcome": (
                "accepted"
                if accepted
                else "idle" if not session.get("running") else "rejected"
            ),
            "text": text,
        },
    )


@method("session.queue.snapshot")
def _(rid, params: dict) -> dict:
    if err := _require_cxba_private(rid):
        return err
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    if session.get("cxba_binding") is None:
        return _err(rid, 4037, "prompt queue requires a CXBA session")
    sid = str(params.get("session_id") or "")
    snapshot = _cxba_prompt_queue_snapshot(session)
    if not session.get("running"):
        from tui_gateway.cxba_runtime import run_for_session

        if run_for_session(sid) is None:
            _request_cxba_prompt_claim(sid, session)
        snapshot = _cxba_prompt_queue_snapshot(session)
    return _ok(rid, snapshot)


@method("session.queue.enqueue")
def _(rid, params: dict) -> dict:
    if err := _require_cxba_private(rid):
        return err
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    if session.get("cxba_binding") is None:
        return _err(rid, 4037, "prompt queue requires a CXBA session")
    text = str(params.get("text") or "").strip()
    prompt = str(params.get("prompt") or "").strip()
    actor_user_id = str(params.get("actor_user_id") or "").strip()
    if not text or not prompt or not actor_user_id:
        return _err(rid, 4002, "text, prompt and actor_user_id are required")
    sid = str(params.get("session_id") or "")
    from tui_gateway.cxba_runtime import run_for_session

    active_run = run_for_session(sid)
    inbox = _cxba_prompt_inbox(session)
    with session["history_lock"]:
        if not session.get("running") and active_run is None:
            return _err(rid, 4095, "Session is idle; submit a new Run instead")
        item = {
            "message_id": uuid.uuid4().hex,
            "text": text,
            "prompt": prompt,
            "attachments": list(params.get("attachments") or []),
            "actor_user_id": actor_user_id,
            "created_at": time.time(),
            "reasoning_enabled": bool(params.get("reasoning_enabled")),
        }
        inbox.append(item)
    try:
        _persist_cxba_prompt_inbox(session)
    except Exception as exc:
        with session["history_lock"]:
            inbox[:] = [queued for queued in inbox if queued.get("message_id") != item["message_id"]]
        return _err(rid, 5008, f"failed to persist prompt queue: {exc}")
    snapshot = _emit_cxba_prompt_queue(str(params.get("session_id") or ""), session)
    return _ok(rid, {"item": _public_cxba_prompt_item(item), **snapshot})


@method("session.queue.update")
def _(rid, params: dict) -> dict:
    if err := _require_cxba_private(rid):
        return err
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    message_id = str(params.get("message_id") or "").strip()
    text = str(params.get("text") or "").strip()
    prompt = str(params.get("prompt") or "").strip()
    if not message_id or not text or not prompt:
        return _err(rid, 4002, "message_id, text and prompt are required")
    inbox = _cxba_prompt_inbox(session)
    with session["history_lock"]:
        if session.get("_cxba_prompt_claim_pending") == message_id:
            return _err(rid, 4096, "prompt is being claimed")
        item = next((queued for queued in inbox if queued.get("message_id") == message_id), None)
        if item is None:
            return _err(rid, 4007, "queued prompt not found")
        item["text"] = text
        item["prompt"] = prompt
        item["attachments"] = list(params.get("attachments") or [])
        item["reasoning_enabled"] = bool(params.get("reasoning_enabled"))
    try:
        _persist_cxba_prompt_inbox(session)
    except Exception as exc:
        return _err(rid, 5008, f"failed to persist prompt queue: {exc}")
    return _ok(rid, _emit_cxba_prompt_queue(str(params.get("session_id") or ""), session))


@method("session.queue.delete")
def _(rid, params: dict) -> dict:
    if err := _require_cxba_private(rid):
        return err
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    message_id = str(params.get("message_id") or "").strip()
    inbox = _cxba_prompt_inbox(session)
    with session["history_lock"]:
        if session.get("_cxba_prompt_claim_pending") == message_id:
            return _err(rid, 4096, "prompt is being claimed")
        original_size = len(inbox)
        inbox[:] = [item for item in inbox if item.get("message_id") != message_id]
        if len(inbox) == original_size:
            return _err(rid, 4007, "queued prompt not found")
    try:
        _persist_cxba_prompt_inbox(session)
    except Exception as exc:
        return _err(rid, 5008, f"failed to persist prompt queue: {exc}")
    return _ok(rid, _emit_cxba_prompt_queue(str(params.get("session_id") or ""), session))


@method("session.queue.steer")
def _(rid, params: dict) -> dict:
    if err := _require_cxba_private(rid):
        return err
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    sid = str(params.get("session_id") or "")
    message_id = str(params.get("message_id") or "").strip()
    if "case_context" not in params:
        return _err(rid, 4039, "queued steer requires a trusted case_context")
    from tui_gateway.cxba_runtime import case_context_model_prompt, run_for_session, update_run_case_context

    run = run_for_session(sid)
    if run is None:
        return _err(rid, 4094, "CXBA Run is terminal or detached")
    try:
        run = update_run_case_context(sid, session.get("cxba_binding"), params.get("case_context"))
    except ValueError as exc:
        return _err(rid, 4039, f"invalid trusted case_context: {exc}")
    inbox = _cxba_prompt_inbox(session)
    with session["history_lock"]:
        if session.get("_cxba_prompt_claim_pending") == message_id:
            return _err(rid, 4096, "prompt is being claimed")
        item = next((queued for queued in inbox if queued.get("message_id") == message_id), None)
        if item is None:
            return _err(rid, 4007, "queued prompt not found")
        prompt = str(item.get("prompt") or item.get("text") or "").strip()
    model_text = prompt
    if current_case_note := case_context_model_prompt(run.case_context):
        model_text = f"{current_case_note}\n\n{prompt}"
    if session.get("running"):
        agent = session.get("agent")
        try:
            accepted = bool(agent is not None and hasattr(agent, "steer") and agent.steer(model_text))
        except Exception as exc:
            return _err(rid, 5000, f"steer failed: {exc}")
        if not accepted:
            return _err(rid, 4097, "agent did not accept steer")
    else:
        with session["history_lock"]:
            if session.get("running"):
                return _err(rid, 4009, "session busy")
            session["running"] = True
        if not _run_prompt_submit(
            rid,
            sid,
            session,
            model_text,
            display_kind="cxba_read_only_continuation",
            trusted_run_context=run.context,
            trusted_case_context=run.case_context,
        ):
            with session["history_lock"]:
                session["running"] = False
            return _err(rid, 5031, "queued steer continuation could not start")
    with session["history_lock"]:
        inbox[:] = [queued for queued in inbox if queued.get("message_id") != message_id]
        if session.get("inflight_turn"):
            _record_inflight_correction(session, prompt)
        session["last_active"] = time.time()
    try:
        _persist_cxba_prompt_inbox(session)
    except Exception as exc:
        return _err(rid, 5008, f"steer accepted but queue persistence failed: {exc}")
    _emit("steer.received", sid, {"status": "QUEUED", "queue_message_id": message_id})
    return _ok(rid, {"status": "steered", "control_outcome": "accepted", **_emit_cxba_prompt_queue(sid, session)})


@method("session.queue.claim")
def _(rid, params: dict) -> dict:
    if err := _require_cxba_private(rid):
        return err
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    sid = str(params.get("session_id") or "")
    message_id = str(params.get("message_id") or "").strip()
    trusted_prompt = str(params.get("prompt") or "").strip()
    if not trusted_prompt:
        return _err(rid, 4002, "trusted prompt is required")
    try:
        from tools.run_sandbox import validate_run_context
        from tui_gateway.cxba_runtime import (
            validate_case_context,
            validate_case_context_matches_binding,
            validate_run_matches_binding,
        )

        trusted_run_context = validate_run_context(params.get("run_context"))
        trusted_case_context = validate_case_context(params.get("case_context"))
        validate_run_matches_binding(session.get("cxba_binding"), trusted_run_context)
        validate_case_context_matches_binding(session.get("cxba_binding"), trusted_case_context)
    except (OSError, ValueError) as exc:
        return _err(rid, 4039, f"invalid trusted queue claim context: {exc}")
    inbox = _cxba_prompt_inbox(session)
    with session["history_lock"]:
        if session.get("running"):
            return _err(rid, 4009, "session busy")
        if not inbox or inbox[0].get("message_id") != message_id:
            return _err(rid, 4096, "only the first queued prompt can be claimed")
        if session.get("_cxba_prompt_claim_pending") not in (None, "", message_id):
            return _err(rid, 4096, "another prompt is being claimed")
        item = dict(inbox[0])
        session["running"] = True
        session["_turn_cancel_requested"] = False
        session["_safe_stop_requested"] = False
        session["_cxba_prompt_claim_pending"] = message_id
    started = _run_prompt_submit(
        rid,
        sid,
        session,
        trusted_prompt,
        trusted_run_context=trusted_run_context,
        trusted_case_context=trusted_case_context,
    )
    if not started:
        with session["history_lock"]:
            session["running"] = False
            session.pop("_cxba_prompt_claim_pending", None)
        return _err(rid, 5031, "queued prompt could not start")
    with session["history_lock"]:
        if inbox and inbox[0].get("message_id") == message_id:
            inbox.pop(0)
        session.pop("_cxba_prompt_claim_pending", None)
    try:
        _persist_cxba_prompt_inbox(session)
    except Exception as exc:
        return _err(rid, 5008, f"queued prompt started but queue persistence failed: {exc}")
    return _ok(rid, {"status": "streaming", "message_id": message_id, **_emit_cxba_prompt_queue(sid, session)})


@method("session.queue.reject")
def _(rid, params: dict) -> dict:
    if err := _require_cxba_private(rid):
        return err
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    sid = str(params.get("session_id") or "")
    message_id = str(params.get("message_id") or "").strip()
    reason = str(params.get("reason") or "queued prompt rejected").strip()
    inbox = _cxba_prompt_inbox(session)
    with session["history_lock"]:
        if not inbox or inbox[0].get("message_id") != message_id:
            return _err(rid, 4096, "only the first queued prompt can be rejected")
        rejected = inbox.pop(0)
        session.pop("_cxba_prompt_claim_pending", None)
    try:
        _persist_cxba_prompt_inbox(session)
    except Exception as exc:
        return _err(rid, 5008, f"failed to persist prompt queue: {exc}")
    write_json(_event_frame("session.queue.rejected", sid, {"item": _public_cxba_prompt_item(rejected), "reason": reason}))
    snapshot = _emit_cxba_prompt_queue(sid, session)
    _request_cxba_prompt_claim(sid, session)
    return _ok(rid, snapshot)


@method("session.queue.release")
def _(rid, params: dict) -> dict:
    """Release a failed control-plane claim without dropping the prompt."""
    if err := _require_cxba_private(rid):
        return err
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    sid = str(params.get("session_id") or "")
    message_id = str(params.get("message_id") or "").strip()
    inbox = _cxba_prompt_inbox(session)
    with session["history_lock"]:
        if not inbox or inbox[0].get("message_id") != message_id:
            return _err(rid, 4096, "only the first queued prompt can be released")
        if session.get("_cxba_prompt_claim_pending") != message_id:
            return _err(rid, 4096, "queued prompt is not being claimed")
        session.pop("_cxba_prompt_claim_pending", None)
    return _ok(rid, _emit_cxba_prompt_queue(sid, session))


@method("session.redirect")
def _(rid, params: dict) -> dict:
    """Redirect the active model turn while preserving valid work/context."""
    text = (params.get("text") or "").strip()
    if not text:
        return _err(rid, 4002, "text is required")
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    sid = str(params.get("session_id") or "")
    cxba_run = None
    model_text = text
    if _is_cxba_run_session(sid):
        from tui_gateway.cxba_runtime import (
            case_context_model_prompt,
            run_for_session,
            update_run_case_context,
        )

        cxba_run = run_for_session(sid)
        if cxba_run is None:
            return _err(rid, 4094, "CXBA Run is terminal or detached")
        if cxba_run.status == "SAFE_STOPPING":
            return _err(rid, 4094, "CXBA Run is stopping and cannot be redirected")
        if "case_context" not in params:
            return _err(rid, 4039, "CXBA Run control requires a trusted case_context")
        try:
            cxba_run = update_run_case_context(
                sid, session.get("cxba_binding"), params.get("case_context")
            )
        except ValueError as exc:
            return _err(rid, 4039, f"invalid trusted case_context: {exc}")
        if current_case_note := case_context_model_prompt(cxba_run.case_context):
            model_text = f"{current_case_note}\n\n{text}"
    agent = session.get("agent")
    # Turn-build window: a fresh turn flips running=True and kicks off an async
    # agent build, so session["agent"] is briefly None. That is not an
    # unsupported runtime — queue the correction server-side so it reaches the
    # model as the next turn, instead of a misleading 4010 the client silently
    # swallows into a lost follow-up.
    if agent is None and session.get("running"):
        _enqueue_prompt(
            session,
            text,
            current_transport() or _stdio_transport,
            trusted_run_context=cxba_run.context if cxba_run is not None else None,
            trusted_case_context=cxba_run.case_context if cxba_run is not None else None,
        )
        session["last_active"] = time.time()
        return _ok(
            rid,
            {"status": "queued", "control_outcome": "accepted", "text": text},
        )
    if (
        agent is None
        or getattr(agent, "_supports_active_turn_redirect", False) is not True
        or not hasattr(agent, "redirect")
    ):
        return _err(rid, 4010, "agent does not support active-turn redirect")
    try:
        accepted = agent.redirect(model_text)
    except Exception as exc:
        return _err(rid, 5000, f"redirect failed: {exc}")
    if accepted:
        with session["history_lock"]:
            _record_inflight_correction(session, text)
            session["last_active"] = time.time()
    return _ok(
        rid,
        {
            "status": "redirected" if accepted else "rejected",
            "control_outcome": (
                "accepted"
                if accepted
                else "idle" if not session.get("running") else "rejected"
            ),
            "text": text,
        },
    )


@method("terminal.resize")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    session["cols"] = int(params.get("cols", 80))
    return _ok(rid, {"cols": session["cols"]})


def register(server) -> None:
    """Bind this module's handlers onto ``server``'s globals and registry."""
    _registry.install(server)
