---
template_id: company_strategy_ops_analysis
category: scenario
summary: Executive strategy and operations analysis template for annual strategy reviews, quarterly business reviews, budget execution, KPI tracking, and board-level operating meetings.
keywords: [strategy, operations, kpi, executive, business-analysis]
primary_color: "#12355B"
canvas_format: ppt169
replication_mode: standard
use_cases: 年度战略经营分析、季度经营复盘、董事会/经营会汇报、预算执行分析、战略举措跟踪
design_tone: 稳健、数据驱动、管理驾驶舱风格，兼具战略咨询感和企业经营汇报的正式感
placeholders:
  01_cover: ["{{TITLE}}", "{{SUBTITLE}}", "{{BRAND_LOGO}}", "{{REPORT_PERIOD}}", "{{AUTHOR}}", "{{DATE}}"]
  02_toc: ["{{TOC_ITEM_1_TITLE}}", "{{TOC_ITEM_2_TITLE}}", "{{TOC_ITEM_3_TITLE}}", "{{TOC_ITEM_4_TITLE}}", "{{TOC_ITEM_5_TITLE}}"]
  02_chapter: ["{{CHAPTER_NUM}}", "{{CHAPTER_TITLE}}", "{{CHAPTER_DESC}}"]
  03_content: ["{{PAGE_TITLE}}", "{{KEY_MESSAGE}}", "{{CONTENT_AREA}}", "{{SECTION_NAME}}", "{{SOURCE}}", "{{PAGE_NUM}}"]
  03a_content_kpi_dashboard: ["{{PAGE_TITLE}}", "{{KEY_MESSAGE}}", "{{KPI_1_VALUE}}", "{{KPI_1_LABEL}}", "{{KPI_2_VALUE}}", "{{KPI_2_LABEL}}", "{{KPI_3_VALUE}}", "{{KPI_3_LABEL}}", "{{KPI_4_VALUE}}", "{{KPI_4_LABEL}}", "{{SECTION_NAME}}", "{{PAGE_NUM}}"]
  03b_content_strategy_matrix: ["{{PAGE_TITLE}}", "{{KEY_MESSAGE}}", "{{MATRIX_X_LABEL}}", "{{MATRIX_Y_LABEL}}", "{{QUADRANT_1_TITLE}}", "{{QUADRANT_2_TITLE}}", "{{QUADRANT_3_TITLE}}", "{{QUADRANT_4_TITLE}}", "{{SECTION_NAME}}", "{{PAGE_NUM}}"]
  03c_content_roadmap: ["{{PAGE_TITLE}}", "{{KEY_MESSAGE}}", "{{PHASE_1_TITLE}}", "{{PHASE_2_TITLE}}", "{{PHASE_3_TITLE}}", "{{PHASE_4_TITLE}}", "{{SECTION_NAME}}", "{{PAGE_NUM}}"]
  04_ending: ["{{THANK_YOU}}", "{{ENDING_SUBTITLE}}", "{{CONTACT_INFO}}", "{{COPYRIGHT}}"]
---

# 公司战略经营分析模板 — Design Specification

## I. Template Overview

- Use cases: 年度战略经营分析、季度经营复盘、董事会/经营会汇报、预算执行分析、战略举措跟踪。
- Design tone: 稳健、数据驱动、管理驾驶舱风格，兼具战略咨询感和企业经营汇报的正式感。
- Theme mode: light theme, with a white and cool-gray operating surface anchored by deep navy executive headers and teal action highlights.
- At a glance, the template is identified by a deep navy top rule, teal decision markers, compact KPI cards, and thin grid lines that evoke a boardroom dashboard rather than a decorative corporate brochure.

## II. Color Scheme

| Role | Color | Usage |
| --- | --- | --- |
| Primary navy | `#12355B` | Executive header bars, major titles, chapter bands |
| Accent teal | `#00A88E` | Decision markers, positive KPI highlights, timeline nodes |
| Strategic blue | `#2F6BFF` | Data callouts, quadrant axes, secondary emphasis |
| Warning amber | `#F59E0B` | Risk, attention, budget variance |
| Operating red | `#E5484D` | Negative variance, constraint flags |
| Background | `#F7F9FC` | Page base and dashboard panels |
| Card surface | `#FFFFFF` | Reusable content cards |
| Grid line | `#D8E1EA` | Dividers, matrix rules, chart scaffolding |
| Primary text | `#1F2937` | Body text and labels |
| Secondary text | `#64748B` | Footers, notes, axis labels |

## III. Typography

- Font stack: `Arial, "Microsoft YaHei", sans-serif`.
- Body baseline: 18 px for executive prose and 14-16 px for dense dashboard labels.
- Large KPI values use 34-46 px bold numerals; page titles use 30-36 px bold text.

## IV. Signature Design Elements

- **Executive Header Rule**: a 10 px navy top band with a short teal segment at the left, used on all analytical pages.
- **Key Message Strip**: a slim rounded rectangle below the page title, carrying the conclusion-first sentence in navy text with a teal lead marker.
- **Dashboard Card System**: white cards with 1 px grid-line borders, 6-8 px corner radius, small uppercase labels, and large numeric values.
- **Strategic Axis Motif**: thin blue/gray matrix lines with teal and amber quadrant tags for strategic choices, opportunity/risk, and capability/market fit.
- **Operating Footer**: left section label, center source note, and right page number in a small navy pill.

## V. Page Roster

| File | Description |
| --- | --- |
| `01_cover.svg` | Cover page with navy executive header, brand placeholder, large report title, period tag, and four strategic analysis pillars. |
| `02_toc.svg` | Table of contents page using a left chapter rail and a right-side executive summary panel for meeting context or key agenda metrics. |
| `02_chapter.svg` | Chapter divider with large chapter number, navy panel, teal decision line, and short strategic description. |
| `03_content.svg` | Flexible conclusion-first content page with title, key message strip, large open content area, and operating footer. |
| `03a_content_kpi_dashboard.svg` | KPI dashboard variant with four metric cards, variance chips, mini trend rails, and lower insight/risk panels. |
| `03b_content_strategy_matrix.svg` | Strategy matrix variant for SWOT, portfolio choices, capability-market fit, or priority ranking. |
| `03c_content_roadmap.svg` | Strategic roadmap variant with four phases, milestones, owner/risk slots, and decision checkpoint band. |
| `04_ending.svg` | Closing page with restrained navy frame, thank-you message, contact slot, and copyright/footer note. |

## VI. Placeholder Overrides

This template uses `{{KEY_MESSAGE}}` on analytical pages to enforce conclusion-first executive communication. Dashboard and matrix variants declare explicit placeholders in frontmatter so downstream Strategist can select the right page by analysis task.
