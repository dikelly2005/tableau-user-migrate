# Test Environment Setup Guide

This document defines the Tableau Cloud test environment required to fully exercise every code path in the `tableau-user-migrate` tool across all modes (dry-run, clone, migrate, clean-only).

---

## 1. Users

Create **10 pre-existing users** to cover all classification tiers, role types, and migration scenarios:

| # | Username (email) | Site Role | Purpose |
|---|-----------------|-----------|---------|
| 1 | `TableauTest01@old-domain.com` | SiteAdministratorCreator | **Very-high tier** — owns top-level projects, sets default permissions, governance role |
| 2 | `TableauTest02@old-domain.com` | Creator | **Very-high tier** — owns nested projects, has default permission grants |
| 3 | `TableauTest03@old-domain.com` | Creator | **High tier** — owns >10 content items, owns published datasources |
| 4 | `TableauTest11@old-domain.com` | Creator | **High tier** — owns flows + VCs, moderate content |
| 5 | `TableauTest05@old-domain.com` | Explorer | **Moderate tier** — owns 3–5 items, has favorites/custom views/subscriptions |
| 6 | `TableauTest06@old-domain.com` | ExplorerCanPublish | **Moderate tier** — owns datasources, has alerts, multiple groups |
| 7 | `TableauTest07@old-domain.com` | Viewer | **Low tier** — consumer only, single group, few favorites |
| 8 | `TableauTest08@old-domain.com` | Viewer | **Low tier** — consumer only, group-inherited access, zero explicit perms |
| 9 | `TableauTest09@old-domain.com` | Explorer | **Not migrated** — verifies no collateral damage |
| 10 | `TableauTest10@old-domain.com` | SiteAdministratorCreator | Admin for setup + validation (not migrated) |

**Target users (created by tool — should NOT exist beforehand):**

| # | Username (email) | Maps From |
|---|-----------------|-----------|
| 11 | `TableauTest01@new-domain.com` | User 1 |
| 12 | `TableauTest02@new-domain.com` | User 2 |
| 13 | `TableauTest03@new-domain.com` | User 3 |
| 14 | `TableauTest11@new-domain.com` | User 4 |
| 15 | `TableauTest05@new-domain.com` | User 5 |
| 16 | `TableauTest06@new-domain.com` | User 6 |
| 17 | `TableauTest07@new-domain.com` | User 7 |
| 18 | `TableauTest08@new-domain.com` | User 8 |

---

## 2. Project Structure & Permission Models

Create a **4-level hierarchy** with all three Tableau permission models represented:

```
├── Corporate/                           ← LOCKED & INHERITED | owned by TableauTest01
│   ├── Corporate/Finance/               ← LOCKED & INHERITED (forced by parent)
│   │   └── Corporate/Finance/Reports/   ← LOCKED & INHERITED (forced by parent)
│   ├── Corporate/HR/                    ← LOCKED & INHERITED (forced by parent)
│   └── Corporate/Legal/                 ← LOCKED & INHERITED (forced by parent)
│
├── Operations/                          ← LOCKED (not inherited) | owned by TableauTest01
│   │                                       Children can set their own model
│   ├── Operations/Finance Models/       ← CUSTOMIZED (each asset has own perms)
│   ├── Operations/Compensation/         ← CUSTOMIZED (sensitive — per-asset permissions)
│   └── Operations/Compliance/           ← LOCKED & INHERITED (child opts into inheritance)
│
├── Sales/                               ← LOCKED (not inherited) | owned by TableauTest02
│   ├── Sales/North America/             ← LOCKED (not inherited, own defaults)
│   │   └── Sales/North America/Reps/    ← CUSTOMIZED (per-rep workbook permissions)
│   ├── Sales/EMEA/                      ← LOCKED (not inherited, own defaults)
│   └── Sales/APAC/                      ← LOCKED (not inherited, own defaults)
│       └── Sales/APAC/Partners/         ← CUSTOMIZED (partner-specific access)
│
├── Analytics/                           ← CUSTOMIZED | owned by TableauTest02
│   ├── Analytics/Dashboards/            ← CUSTOMIZED (each workbook can differ)
│   ├── Analytics/Self-Service/          ← CUSTOMIZED (open to explorers)
│   └── Analytics/Experiments/           ← CUSTOMIZED (owned by TableauTest11)
│
├── Marketing/                           ← LOCKED (not inherited) | owned by TableauTest09 (control)
│   └── Marketing/Campaigns/             ← LOCKED (not inherited)
│
├── Sandbox/                             ← CUSTOMIZED | owned by TableauTest10
│   └── Sandbox/Prototypes/              ← CUSTOMIZED
│
└── Shared/                              ← CUSTOMIZED | owned by TableauTest10
```

**Total: 23 projects** (7 top-level + 16 nested)

### Permission Model Rules

| Parent Model | What Children Can Be |
|-------------|---------------------|
| **Locked & Inherited** | ALL descendants forced to Locked & Inherited (no override possible) |
| **Locked (not inherited)** | Each child independently chooses: Locked, Customized, or Locked & Inherited |
| **Customized** | Each child independently chooses: Locked, Customized, or Locked & Inherited |

### Permission Model Configuration

| Project | Content Permissions Setting | What It Tests |
|---------|---------------------------|---------------|
| `Corporate` | **Locked & Inherited** | Top-level locked propagates to ALL descendants. No child can override. Default perms set here apply uniformly to all 4 child projects. |
| `Corporate/Finance` | **Locked & Inherited** | Forced by parent — inherits all defaults from Corporate. |
| `Corporate/Finance/Reports` | **Locked & Inherited** | Forced by parent — 3rd-level inheritance, same defaults apply. |
| `Corporate/HR` | **Locked & Inherited** | Forced by parent — inherits from Corporate. |
| `Corporate/Legal` | **Locked & Inherited** | Forced by parent — inherits from Corporate. |
| `Operations` | **Locked (not inherited)** | Own defaults at this level. Children are free to choose their own model. |
| `Operations/Finance Models` | **Customized** | Child opts out of parent's locked model. Each workbook/DS inside has its own explicit perms. Tests mixed: parent is Locked, child is Customized. |
| `Operations/Compensation` | **Customized** | Sensitive leaf — per-asset permissions. Different perms on each sibling content item. |
| `Operations/Compliance` | **Locked & Inherited** | Child opts INTO inheritance under a Locked parent. Tests that a child can voluntarily lock & inherit even when parent doesn't force it. |
| `Sales` | **Locked (not inherited)** | Each Sales sub-project has its own locked permissions set independently. |
| `Sales/North America` | **Locked (not inherited)** | Own locked defaults, independent from Sales parent. |
| `Sales/North America/Reps` | **Customized** | Leaf override under a Locked parent. Per-workbook permissions for individual sales rep dashboards. |
| `Sales/EMEA` | **Locked (not inherited)** | Own locked defaults. |
| `Sales/APAC` | **Locked (not inherited)** | Own locked defaults. |
| `Sales/APAC/Partners` | **Customized** | Leaf override — partner-facing content with per-asset grants to specific users/groups. |
| `Analytics` | **Customized** | Fully open. Per-content permissions on everything inside. |
| `Analytics/Dashboards` | **Customized** | Each dashboard workbook has unique permission sets. |
| `Analytics/Self-Service` | **Customized** | Open to explorers with broad grants. |
| `Analytics/Experiments` | **Customized** | Analyst-owned, narrow access. |
| `Marketing` | **Locked (not inherited)** | Control — not transferred. |
| `Marketing/Campaigns` | **Locked (not inherited)** | Control. |
| `Sandbox` | **Customized** | Open project for prototyping. |
| `Sandbox/Prototypes` | **Customized** | Inherits open model. |
| `Shared` | **Customized** | Broadly accessible. |

### Key Pattern: Locked (not inherited) Parent → Customized Child

Under a Locked (not inherited) parent, each child can independently choose its model. This creates the critical test scenario where siblings under the same parent have different permission models:

- **`Operations/Finance Models`** (Customized): Parent (`Operations`) is Locked with its own defaults, but this child is Customized. Content published here does NOT use Operations' default permissions — each workbook, datasource, flow, or VC gets explicit per-asset permissions.
- **`Operations/Compensation`** (Customized): Same pattern — sensitive compensation data has per-asset grants. Sibling to Finance Models but with completely different access rules per item.
- **`Operations/Compliance`** (Locked & Inherited): Same parent, but this child OPTS INTO locked inheritance — demonstrating all three models coexisting under one parent.
- **`Sales/North America/Reps`** (Customized): Parent (`Sales/NA`) is Locked with its own defaults, but this leaf allows per-rep workbook permissions.
- **`Sales/APAC/Partners`** (Customized): Parent (`Sales/APAC`) is Locked, but partner content needs individual access control.

This tests that the migration tool correctly:
1. Clones default permissions for Locked & Inherited projects (at the single top-level set point — all descendants inherit)
2. Clones default permissions for Locked (not inherited) projects (each project has its own set of defaults)
3. Clones explicit per-asset permissions for Customized projects
4. Does NOT attempt to clone explicit content perms on Locked projects (they don't exist — access is governed only by defaults)
5. Handles users who have access via BOTH models (e.g., `TableauTest05` has Operations defaults via group AND explicit perms on `Operations/Finance Models` content)

---

## 3. Groups

Create **7 custom groups** (excludes "All Users"):

| Group Name | Members | Notes |
|-----------|---------|-------|
| `Finance Team` | TableauTest01, TableauTest05, TableauTest06, TableauTest09 | Cross-tier membership |
| `Sales Team` | TableauTest02, TableauTest03, TableauTest05, TableauTest06 | Heavy overlap |
| `Analytics Team` | TableauTest11, TableauTest05, TableauTest03 | Creator-heavy |
| `Data Consumers` | TableauTest07, TableauTest08, TableauTest05, TableauTest09 | Viewer-heavy |
| `Leadership` | TableauTest01, TableauTest02, TableauTest10 | Admin-heavy |
| `All Creators` | TableauTest01, TableauTest02, TableauTest03, TableauTest11, TableauTest06 | Role-based |
| `Temp Project Group` | TableauTest07, TableauTest05 | Small group — tests 2-member group |

This tests:
- Users in 1, 2, 3, and 4+ groups
- Group membership overlap between migrated and non-migrated users
- Groups with only migrated users vs. mixed
- The "All Users" skip logic (automatic group)

---

## 4. Workbooks

Create **30 workbooks**:

| # | Name | Project | Owner | Tabs? | Notes |
|---|------|---------|-------|-------|-------|
| 1 | `CEO P` | Corporate/Finance/Reports | TableauTest01 | Yes (3 tabs) | Locked inherited perms |
| 2 | `Budget vs Actual` | Corporate/Finance/Reports | TableauTest01 | Yes (2 tabs) | Locked inherited perms |
| 3 | `Valuation Model` | Operations/Finance Models | TableauTest01 | No | **CUSTOMIZED leaf** — has own explicit perms |
| 4 | `Forecast Model` | Operations/Finance Models | TableauTest11 | No | **CUSTOMIZED leaf** — different owner, different perms |
| 5 | `Headcount Planning` | Corporate/HR | TableauTest01 | No | Locked inherited |
| 6 | `Comp Benchmarks` | Operations/Compensation | TableauTest01 | No | **CUSTOMIZED leaf** — restricted per-asset |
| 7 | `Exec Comp Detail` | Operations/Compensation | TableauTest01 | No | **CUSTOMIZED leaf** — even more restricted |
| 8 | `Compliance Dashboard` | Operations/Compliance | TableauTest01 | Yes (4 tabs) | Locked & Inherited (child opts in), tests tabbed view perm skip |
| 9 | `NA Pipeline` | Sales/North America | TableauTest02 | No | Locked project perms |
| 10 | `EMEA Pipeline` | Sales/EMEA | TableauTest02 | No | Locked project perms |
| 11 | `APAC Pipeline` | Sales/APAC | TableauTest02 | No | Locked project perms |
| 12 | `Rep: Jones` | Sales/North America/Reps | TableauTest03 | No | **CUSTOMIZED leaf** — only Jones + manager can see |
| 13 | `Rep: Smith` | Sales/North America/Reps | TableauTest03 | No | **CUSTOMIZED leaf** — only Smith + manager can see |
| 14 | `Rep: Garcia` | Sales/North America/Reps | TableauTest03 | No | **CUSTOMIZED leaf** — only Garcia + manager can see |
| 15 | `Partner: Acme` | Sales/APAC/Partners | TableauTest02 | No | **CUSTOMIZED leaf** — partner-specific view |
| 16 | `Partner: GlobalCo` | Sales/APAC/Partners | TableauTest02 | No | **CUSTOMIZED leaf** — different partner perms |
| 17 | `Sales Forecasting` | Sales | TableauTest03 | Yes (2 tabs) | Locked |
| 18 | `Win Rate Analysis` | Sales | TableauTest03 | No | Locked |
| 19 | `Deal Flow Tracker` | Sales/North America | TableauTest03 | No | Locked |
| 20 | `Territory Map` | Sales/EMEA | TableauTest03 | No | Locked |
| 21 | `Customer Segments` | Sales/APAC | TableauTest03 | No | Locked |
| 22 | `Renewal Rates` | Sales | TableauTest03 | No | Locked |
| 23 | `Quota Attainment` | Sales | TableauTest03 | No | 11th item for producer — pushes to high tier |
| 24 | `KPI Dashboard` | Analytics/Dashboards | TableauTest11 | Yes (5 tabs) | Customized perms |
| 25 | `Experiment Results` | Analytics/Experiments | TableauTest11 | No | Customized |
| 26 | `Self-Service Explore` | Analytics/Self-Service | TableauTest05 | No | Customized, moderate-tier ownership |
| 27 | `My Exploration` | Analytics/Self-Service | TableauTest05 | No | Customized |
| 28 | `Campaign Performance` | Marketing/Campaigns | TableauTest09 | No | Control — not transferred |
| 29 | `Prototype Alpha` | Sandbox/Prototypes | TableauTest06 | No | Customized project |
| 30 | `Personal Notebook` | *(Personal Space)* | TableauTest03 | No | Tests Personal Space path |

**Views per workbook**: Tabbed workbooks have their tab count as views. Non-tabbed workbooks have 2 views (1 sheet + 1 dashboard). **Total views: ~68**

---

## 5. Published Datasources

Create **11 published datasources**:

| # | Name | Project | Owner | Notes |
|---|------|---------|-------|-------|
| 1 | `Corporate Finance DS` | Corporate/Finance | TableauTest01 | Locked inherited perms |
| 2 | `HR Metrics DS` | Corporate/HR | TableauTest01 | Locked inherited perms |
| 3 | `Comp Data DS` | Operations/Compensation | TableauTest01 | **CUSTOMIZED leaf** — restricted access |
| 4 | `Model Inputs DS` | Operations/Finance Models | TableauTest11 | **CUSTOMIZED leaf** — different perms than parent |
| 5 | `Sales Pipeline DS` | Sales | TableauTest03 | Locked project perms |
| 6 | `Customer Data DS` | Sales/North America | TableauTest03 | Locked |
| 7 | `Partner Feed DS` | Sales/APAC/Partners | TableauTest02 | **CUSTOMIZED leaf** — per-partner grants |
| 8 | `Analytics Warehouse DS` | Analytics | TableauTest11 | Customized explicit perms |
| 9 | `Self-Service Extract` | Analytics/Self-Service | TableauTest06 | ExplorerCanPublish test |
| 10 | `Marketing Leads DS` | Marketing | TableauTest09 | Control — not transferred |
| 11 | `Shared Reference DS` | Shared | TableauTest05 | Moderate-tier DS ownership |

---

## 6. Flows

Create **6 flows**:

| # | Name | Project | Owner | Notes |
|---|------|---------|-------|-------|
| 1 | `Finance ETL` | Corporate/Finance | TableauTest01 | Locked inherited |
| 2 | `Comp Refresh Flow` | Operations/Compensation | TableauTest01 | **CUSTOMIZED leaf** — restricted flow |
| 3 | `Sales Data Prep` | Sales | TableauTest03 | Locked |
| 4 | `Analytics Pipeline` | Analytics | TableauTest11 | Customized |
| 5 | `Customer Enrichment` | Sales/North America | TableauTest11 | Locked |
| 6 | `Marketing Flow` | Marketing | TableauTest09 | Control |

---

## 7. Virtual Connections

Create **3 virtual connections**:

| # | Name | Owner | Notes |
|---|------|-------|-------|
| 1 | `Corporate VC` | TableauTest01 | Tests VC ownership transfer + enrichment pass |
| 2 | `Sales VC` | TableauTest11 | Tests revision-based owner resolution |
| 3 | `Analytics VC` | TableauTest11 | Multiple VC ownership for same user |

---

## 8. Explicit Permissions (Customized Projects & Customized Leaf Projects)

These permissions exist on content within **Customized** projects and **Customized leaf** projects (where the parent is Locked but the leaf overrides to per-content permissions).

### 8a. Customized Leaf Projects Under Locked Parents

These demonstrate that each asset within a customized leaf can have completely different permissions — even when the parent project is locked:

**`Operations/Finance Models` (customized leaf under Locked Operations):**

| Content | Grantee | Capabilities | Notes |
|---------|---------|-------------|-------|
| `Valuation Model` (workbook) | TableauTest02 (user) | Read:Allow, Write:Allow, ExportData:Allow | Full access for project lead |
| `Valuation Model` (workbook) | Finance Team (group) | Read:Allow | Read-only for broader team |
| `Valuation Model` (workbook) | TableauTest11 (user) | Read:Allow, Write:Allow | Co-editor |
| `Forecast Model` (workbook) | TableauTest11 (user) | Read:Allow, Write:Allow, ExportData:Allow | Owner has full control |
| `Forecast Model` (workbook) | TableauTest01 (user) | Read:Allow | Executive read-only (different from Valuation!) |
| `Forecast Model` (workbook) | Leadership (group) | Read:Allow, Filter:Allow | Leadership gets filters here but not on Valuation |
| `Model Inputs DS` (datasource) | TableauTest11 (user) | Read:Allow, Connect:Allow, Write:Allow | DS owner |
| `Model Inputs DS` (datasource) | TableauTest01 (user) | Read:Allow, Connect:Allow | Can query but not overwrite |
| `Model Inputs DS` (datasource) | Finance Team (group) | Connect:Deny | Explicitly denied — sensitive inputs |

**`Operations/Compensation` (customized leaf under Locked Operations):**

| Content | Grantee | Capabilities | Notes |
|---------|---------|-------------|-------|
| `Comp Benchmarks` (workbook) | TableauTest01 (user) | Read:Allow, Write:Allow, ExportData:Allow | Full access |
| `Comp Benchmarks` (workbook) | Leadership (group) | Read:Allow | Execs can view benchmarks |
| `Comp Benchmarks` (workbook) | TableauTest05 (user) | Read:Allow, ExportData:Deny | Can view but not export |
| `Exec Comp Detail` (workbook) | TableauTest01 (user) | Read:Allow, Write:Allow | Only govadmin — highly restricted |
| `Exec Comp Detail` (workbook) | TableauTest02 (user) | Read:Deny | Explicitly denied! (tests Deny on read) |
| `Comp Data DS` (datasource) | TableauTest01 (user) | Read:Allow, Connect:Allow, Write:Allow | Only owner |
| `Comp Data DS` (datasource) | Leadership (group) | Read:Allow, Connect:Deny | Can see metadata but not connect |
| `Comp Refresh Flow` (flow) | TableauTest01 (user) | Read:Allow, Execute:Allow | Only owner runs it |

**`Sales/North America/Reps` (customized leaf under Locked Sales/NA):**

| Content | Grantee | Capabilities | Notes |
|---------|---------|-------------|-------|
| `Rep: Jones` (workbook) | TableauTest05 (user) | Read:Allow, Filter:Allow | "Jones" can see their own |
| `Rep: Jones` (workbook) | TableauTest03 (user) | Read:Allow, Write:Allow | Manager has edit |
| `Rep: Smith` (workbook) | TableauTest06 (user) | Read:Allow, Filter:Allow | "Smith" can see their own |
| `Rep: Smith` (workbook) | TableauTest03 (user) | Read:Allow, Write:Allow | Manager has edit |
| `Rep: Garcia` (workbook) | TableauTest07 (user) | Read:Allow | "Garcia" view-only |
| `Rep: Garcia` (workbook) | TableauTest03 (user) | Read:Allow, Write:Allow | Manager has edit |
| `Rep: Garcia` (workbook) | Sales Team (group) | Read:Deny | Explicitly hidden from broader team |

**`Sales/APAC/Partners` (customized leaf under Locked Sales/APAC):**

| Content | Grantee | Capabilities | Notes |
|---------|---------|-------------|-------|
| `Partner: Acme` (workbook) | TableauTest05 (user) | Read:Allow, Filter:Allow | Acme partner rep |
| `Partner: Acme` (workbook) | TableauTest02 (user) | Read:Allow, Write:Allow | Sales leadership |
| `Partner: Acme` (workbook) | Data Consumers (group) | Read:Deny | Hidden from general consumers |
| `Partner: GlobalCo` (workbook) | TableauTest06 (user) | Read:Allow, Filter:Allow | GlobalCo partner rep |
| `Partner: GlobalCo` (workbook) | TableauTest02 (user) | Read:Allow, Write:Allow | Sales leadership |
| `Partner: GlobalCo` (workbook) | TableauTest07 (user) | Read:Deny | Explicitly denied |
| `Partner Feed DS` (datasource) | TableauTest02 (user) | Read:Allow, Connect:Allow, Write:Allow | DS owner |
| `Partner Feed DS` (datasource) | TableauTest05 (user) | Read:Allow, Connect:Allow | Acme rep reads |
| `Partner Feed DS` (datasource) | TableauTest06 (user) | Read:Allow, Connect:Allow | GlobalCo rep reads |

### 8b. Fully Customized Projects (Analytics, Sandbox, Shared)

| Content | Grantee | Capabilities |
|---------|---------|-------------|
| `KPI Dashboard` (workbook) | TableauTest05 (user) | Read:Allow, Filter:Allow, ExportImage:Allow |
| `KPI Dashboard` (workbook) | Analytics Team (group) | Read:Allow |
| `KPI Dashboard` (workbook) | TableauTest07 (user) | Read:Allow, ExportData:Deny |
| `Experiment Results` (workbook) | TableauTest03 (user) | Read:Allow, Write:Allow |
| `Self-Service Explore` (workbook) | Data Consumers (group) | Read:Allow, Filter:Allow |
| `Self-Service Explore` (workbook) | TableauTest06 (user) | Read:Allow, Write:Allow, ExportData:Allow |
| `Analytics Warehouse DS` (datasource) | TableauTest03 (user) | Read:Allow, Connect:Allow |
| `Analytics Warehouse DS` (datasource) | TableauTest05 (user) | Read:Allow, Connect:Deny |
| `Analytics Warehouse DS` (datasource) | Sales Team (group) | Read:Allow |
| `Self-Service Extract` (datasource) | Data Consumers (group) | Read:Allow, Connect:Allow |
| `Shared Reference DS` (datasource) | All Creators (group) | Read:Allow, Connect:Allow |
| `Prototype Alpha` (workbook) | TableauTest07 (user) | Read:Allow |

### 8c. Project-Level Permissions

| Content | Grantee | Capabilities |
|---------|---------|-------------|
| `Analytics` (project) | TableauTest11 (user) | ProjectLeader:Allow |
| `Analytics` (project) | Analytics Team (group) | Read:Allow |
| `Sandbox` (project) | Temp Project Group (group) | Read:Allow, Write:Allow |
| `Shared` (project) | Data Consumers (group) | Read:Allow |
| `Corporate VC` (virtual connection) | TableauTest05 (user) | Read:Allow, Connect:Allow |
| `Corporate VC` (virtual connection) | Finance Team (group) | Read:Allow |

**Total: 52+ explicit permission rules** covering:
- User + group grantees
- Allow + Deny modes (including Read:Deny, Connect:Deny, ExportData:Deny)
- Multiple capabilities per grant
- **Same project, different assets with completely different permission sets**
- **Same user with different access levels on sibling content**
- Workbooks, datasources, flows, projects, virtual connections

---

## 9. Default Permissions (Locked & Locked-Inherited Projects)

### Corporate (Locked & Inherited) — set at top level, ALL descendants forced to inherit

These defaults apply uniformly to `Corporate`, `Corporate/Finance`, `Corporate/Finance/Reports`, `Corporate/HR`, and `Corporate/Legal`. No child project can override — all are forced to Locked & Inherited.

| Content Type | Grantee | Capabilities |
|-------------|---------|-------------|
| Workbooks | TableauTest01 (user) | Read:Allow, Write:Allow, ExportData:Allow, Filter:Allow |
| Workbooks | Finance Team (group) | Read:Allow, Filter:Allow |
| Workbooks | Leadership (group) | Read:Allow, Write:Allow |
| Datasources | TableauTest01 (user) | Read:Allow, Connect:Allow, Write:Allow |
| Datasources | Finance Team (group) | Read:Allow, Connect:Allow |
| Flows | TableauTest01 (user) | Read:Allow, Execute:Allow |
| Flows | All Creators (group) | Read:Allow |
| Virtual Connections | TableauTest01 (user) | Read:Allow, Connect:Allow |

### Operations (Locked, not inherited) — own defaults at this level

Defaults set here apply only to content published directly in `Operations/` and to `Operations/Compliance` (which opted into Locked & Inherited). They do **NOT** apply to `Operations/Finance Models` or `Operations/Compensation` (both Customized — those use explicit per-asset permissions).

| Content Type | Grantee | Capabilities |
|-------------|---------|-------------|
| Workbooks | TableauTest01 (user) | Read:Allow, Write:Allow, ExportData:Allow |
| Workbooks | Finance Team (group) | Read:Allow, Filter:Allow |
| Workbooks | Leadership (group) | Read:Allow, Write:Allow |
| Datasources | TableauTest01 (user) | Read:Allow, Connect:Allow, Write:Allow |
| Datasources | All Creators (group) | Read:Allow, Connect:Allow |
| Flows | TableauTest01 (user) | Read:Allow, Execute:Allow |

### Sales (Locked, not inherited) — set per sub-project

Defaults set on `Sales`, `Sales/North America`, `Sales/EMEA`, `Sales/APAC`. Do **NOT** apply to customized leaves (`Sales/North America/Reps`, `Sales/APAC/Partners`).

**Sales/ (top level):**
| Content Type | Grantee | Capabilities |
|-------------|---------|-------------|
| Workbooks | TableauTest02 (user) | Read:Allow, Write:Allow, ExportData:Allow |
| Workbooks | Sales Team (group) | Read:Allow, Filter:Allow |
| Datasources | TableauTest02 (user) | Read:Allow, Connect:Allow |
| Datasources | TableauTest03 (user) | Read:Allow, Connect:Allow, Write:Allow |

**Sales/North America:**
| Content Type | Grantee | Capabilities |
|-------------|---------|-------------|
| Workbooks | TableauTest03 (user) | Read:Allow, Write:Allow |
| Workbooks | Data Consumers (group) | Read:Allow |

**Sales/EMEA:**
| Content Type | Grantee | Capabilities |
|-------------|---------|-------------|
| Workbooks | TableauTest03 (user) | Read:Allow, Write:Allow |
| Workbooks | Sales Team (group) | Read:Allow |

**Sales/APAC:**
| Content Type | Grantee | Capabilities |
|-------------|---------|-------------|
| Workbooks | TableauTest03 (user) | Read:Allow, Write:Allow |
| Datasources | Sales Team (group) | Read:Allow, Connect:Allow |

### Marketing (Locked, not inherited) — control

| Content Type | Grantee | Capabilities |
|-------------|---------|-------------|
| Workbooks | TableauTest09 (user) | Read:Allow, Write:Allow |
| Workbooks | Data Consumers (group) | Read:Allow |

**Total: 26+ default permission rules** across Locked & Inherited and Locked (not inherited) project hierarchies. Customized projects have zero defaults — all access is explicit per-asset.

---

## 10. Favorites

Set up **18 favorites** across all favoritable content types:

| User | Favorited Content | Type |
|------|------------------|------|
| TableauTest01 | `Corporate Revenue` | workbook |
| TableauTest01 | `Corporate Finance DS` | datasource |
| TableauTest01 | `Corporate` | project |
| TableauTest01 | `Finance ETL` | flow |
| TableauTest02 | `NA Pipeline` | workbook |
| TableauTest02 | `Sales` | project |
| TableauTest02 | `Sales Pipeline DS` | datasource |
| TableauTest03 | `Sales Forecasting` | workbook |
| TableauTest03 | `Win Rate Analysis` | workbook |
| TableauTest03 | `Customer Data DS` | datasource |
| TableauTest11 | `KPI Dashboard` | workbook |
| TableauTest11 | `Analytics Pipeline` | flow |
| TableauTest05 | `Self-Service Explore` | workbook |
| TableauTest05 | `KPI Dashboard` | workbook |
| TableauTest05 | `Analytics` | project |
| TableauTest05 | `Shared Reference DS` | datasource |
| TableauTest06 | `Prototype Alpha` | workbook |
| TableauTest07 | `Corporate Revenue` | workbook |

**Total: 18 favorites** across 4 content types (workbook, datasource, project, flow).

---

## 11. Subscriptions

Create **8 subscriptions**:

| User | Subject | Content (View) | Schedule |
|------|---------|----------------|----------|
| TableauTest01 | `Weekly Corporate Summary` | `Corporate Revenue` → Sheet 1 | Weekly Monday 8 AM |
| TableauTest01 | `Monthly Budget Review` | `Budget vs Actual` → Sheet 1 | Monthly 1st 9 AM |
| TableauTest02 | `Daily Sales Pipeline` | `NA Pipeline` → Dashboard | Daily 7 AM |
| TableauTest03 | `Weekly Win Rates` | `Win Rate Analysis` → Sheet 1 | Weekly Friday 5 PM |
| TableauTest03 | `Daily Forecast` | `Sales Forecasting` → Tab 1 | Daily 6 AM |
| TableauTest11 | `KPI Daily Digest` | `KPI Dashboard` → Tab 1 | Daily 8 AM |
| TableauTest05 | `Self-Service Weekly` | `Self-Service Explore` → Dashboard | Weekly Wednesday 10 AM |
| TableauTest06 | `Prototype Status` | `Prototype Alpha` → Dashboard | Weekly Monday 9 AM |

**Total: 8 subscriptions** — covers users with 0, 1, and 2+ subscriptions.

### Tableau Schedules (Pre-requisite)

Subscriptions reference Tableau schedule objects. Ensure these schedules exist on the site before creating subscriptions:

| Schedule Name | Frequency | Time |
|--------------|-----------|------|
| `Daily 6AM` | Daily | 6:00 AM |
| `Daily 7AM` | Daily | 7:00 AM |
| `Daily 8AM` | Daily | 8:00 AM |
| `Weekly Monday 8AM` | Weekly (Monday) | 8:00 AM |
| `Weekly Monday 9AM` | Weekly (Monday) | 9:00 AM |
| `Weekly Wednesday 10AM` | Weekly (Wednesday) | 10:00 AM |
| `Weekly Friday 5PM` | Weekly (Friday) | 5:00 PM |
| `Monthly 1st 9AM` | Monthly (1st) | 9:00 AM |

---

## 11a. Extract Refresh & Flow Run Schedules

Content with scheduled extract refreshes or flow runs validates that ownership transfer preserves the schedule association. After migration, the schedule remains but the new owner must re-authenticate data connections.

### Extract Refresh Schedules (Workbooks)

| Workbook | Owner | Schedule | Extract Type | Notes |
|----------|-------|----------|-------------|-------|
| `Corporate Revenue` | TableauTest01 | `Daily 7AM` | Full | High-frequency, validates schedule persists after ownership transfer |
| `Budget vs Actual` | TableauTest01 | `Weekly Monday 8AM` | Full | Weekly cadence |
| `Sales Forecasting` | TableauTest03 | `Daily 6AM` | Incremental | Tests incremental extract schedule |
| `KPI Dashboard` | TableauTest11 | `Daily 8AM` | Full | Multi-tab workbook with schedule |
| `NA Pipeline` | TableauTest02 | `Weekly Monday 9AM` | Full | Locked project content with schedule |
| `Prototype Alpha` | TableauTest06 | `Weekly Friday 5PM` | Full | Customized project content with schedule |

### Extract Refresh Schedules (Published Datasources)

| Datasource | Owner | Schedule | Extract Type | Notes |
|-----------|-------|----------|-------------|-------|
| `Corporate Finance DS` | TableauTest01 | `Daily 7AM` | Full | Primary finance data — critical schedule |
| `Sales Pipeline DS` | TableauTest03 | `Daily 6AM` | Incremental | Incremental refresh on published DS |
| `Customer Data DS` | TableauTest03 | `Weekly Monday 8AM` | Full | Weekly full refresh |
| `Self-Service Extract` | TableauTest06 | `Daily 8AM` | Full | ExplorerCanPublish owner with schedule |
| `Analytics Warehouse DS` | TableauTest11 | `Weekly Monday 9AM` | Full | Customized project DS with schedule |

### Flow Run Schedules

| Flow | Owner | Schedule | Notes |
|------|-------|----------|-------|
| `Finance ETL` | TableauTest01 | `Daily 7AM` | Runs before workbook extracts refresh |
| `Sales Data Prep` | TableauTest03 | `Daily 6AM` | Feeds Sales Pipeline DS |
| `Analytics Pipeline` | TableauTest11 | `Weekly Monday 8AM` | Weekly analytics refresh |
| `Customer Enrichment` | TableauTest11 | `Daily 8AM` | Daily enrichment |
| `Comp Refresh Flow` | TableauTest01 | `Monthly 1st 9AM` | Monthly sensitive data refresh |

### Schedule Coverage Summary

| User | Extract Schedules (WB) | Extract Schedules (DS) | Flow Schedules | Total |
|------|----------------------|----------------------|----------------|-------|
| TableauTest01 | 2 | 1 | 2 | 5 |
| TableauTest02 | 1 | 0 | 0 | 1 |
| TableauTest03 | 1 | 2 | 1 | 4 |
| TableauTest11 | 1 | 1 | 2 | 4 |
| TableauTest05 | 0 | 0 | 0 | 0 |
| TableauTest06 | 1 | 1 | 0 | 2 |
| TableauTest07 | 0 | 0 | 0 | 0 |
| TableauTest08 | 0 | 0 | 0 | 0 |

**Total: 16 scheduled tasks** (6 workbook extracts + 5 datasource extracts + 5 flow runs)

**Post-migration validation**: After ownership transfer, verify that schedules remain attached to the content but flag that credentials need re-establishment by the new owner.

---

## 12. Data Alerts

Create **6 data alerts**:

| Owner | Subject | View | Condition |
|-------|---------|------|-----------|
| TableauTest01 | `Revenue Below Target` | `Corporate Revenue` → Revenue KPI | Value < 1000000 |
| TableauTest01 | `Budget Overrun` | `Budget vs Actual` → Variance | Value > 500000 |
| TableauTest02 | `Pipeline Drop NA` | `NA Pipeline` → Total Pipeline | Value < 100000 |
| TableauTest03 | `Win Rate Decline` | `Win Rate Analysis` → Win % | Value < 0.3 |
| TableauTest11 | `KPI Anomaly` | `KPI Dashboard` → Outlier Tab | Value > 3 |
| TableauTest06 | `Prototype Threshold` | `Prototype Alpha` → Metric | Value > 1000 |

**Total: 6 data alerts** — tests ownership transfer + add-recipient-before-transfer + retry logic. Users have 0, 1, or 2 alerts.

---

## 13. Custom Views

Create **8 custom views**:

| Owner | Custom View Name | Workbook | Set as Default? |
|-------|-----------------|----------|-----------------|
| TableauTest01 | `Executive Summary` | `Corporate Revenue` | Yes (for govadmin) |
| TableauTest01 | `Finance Only` | `Budget vs Actual` | No |
| TableauTest02 | `All Regions View` | `NA Pipeline` | Yes (for projlead) |
| TableauTest03 | `My Territory` | `Deal Flow Tracker` | Yes (for producer) |
| TableauTest03 | `Top Deals` | `Sales Forecasting` | No |
| TableauTest11 | `Experiment A/B` | `Experiment Results` | No |
| TableauTest05 | `Filtered Dashboard` | `Self-Service Explore` | Yes (for explorer) |
| TableauTest06 | `Alpha Config` | `Prototype Alpha` | Yes (for poweruser) |

**Total: 8 custom views** — tests ownership transfer, default-for-user check, and default status migration. Users have 0, 1, and 2+ custom views.

---

## 14. Collections

Create **5 collections**:

| Owner | Collection Name | Items | Permissions |
|-------|----------------|-------|-------------|
| TableauTest01 | `Corporate Executive Pack` | Corporate Revenue, Budget vs Actual, Corporate Finance DS, Headcount Planning | Read: Leadership, Finance Team |
| TableauTest02 | `Sales Leadership View` | NA Pipeline, EMEA Pipeline, APAC Pipeline, Sales Pipeline DS | Read: Sales Team; Read: Leadership |
| TableauTest03 | `My Sales Toolkit` | Sales Forecasting, Win Rate Analysis, Deal Flow Tracker, Customer Data DS | Read: Data Consumers |
| TableauTest05 | `Bookmarks` | KPI Dashboard, Self-Service Explore | *(private — no permissions)* |
| TableauTest06 | `Prototype Collection` | Prototype Alpha, Self-Service Extract | Read: Temp Project Group |

**Total: 5 collections** — tests clone-and-replace with varying item counts (2–4), with and without permissions.

---

## 15. Webhooks

Create **4 webhooks**:

| Owner | Name | Event |
|-------|------|-------|
| TableauTest01 | `Corporate WB Updated` | workbook-updated |
| TableauTest02 | `Sales Content Created` | datasource-created |
| TableauTest03 | `New Sales WB` | workbook-created |
| TableauTest11 | `Flow Run Complete` | flow-run-completed |

---

## 16. Pulse Definitions & Subscriptions

Create **4 Pulse metric definitions** and subscriptions:

| Definition Owner | Metric Name | Subscribers |
|-----------------|-------------|-------------|
| TableauTest01 | `Monthly Revenue` | TableauTest01, TableauTest02, TableauTest05 |
| TableauTest02 | `Sales Pipeline Value` | TableauTest02, TableauTest03 |
| TableauTest09 | `Campaign ROI` | TableauTest06, TableauTest09 |
| TableauTest11 | `KPI Health Score` | TableauTest11, TableauTest05 |

**Total: 4 definitions, 8 pulse subscriptions for migrated users**

---

## 17. CSV Mapping File (`data/user_mappings.csv`)

```csv
old_username,new_username
TableauTest01@old-domain.com,TableauTest01@new-domain.com
TableauTest02@old-domain.com,TableauTest02@new-domain.com
TableauTest03@old-domain.com,TableauTest03@new-domain.com
TableauTest11@old-domain.com,TableauTest11@new-domain.com
TableauTest05@old-domain.com,TableauTest05@new-domain.com
TableauTest06@old-domain.com,TableauTest06@new-domain.com
TableauTest07@old-domain.com,TableauTest07@new-domain.com
TableauTest08@old-domain.com,TableauTest08@new-domain.com
```

### Case-Sensitivity Test Variant (`data/user_mappings_case_test.csv`)

```csv
old_username,new_username
TABLEAUTEST01@OLD-DOMAIN.COM,TableauTest01@new-domain.com
tableautest07@Old-Domain.Com,TableauTest07@new-domain.com
```

---

## 18. Permission Model Coverage Matrix

This matrix shows which permission scenarios each user exercises:

| User | Locked & Inherited | Locked | Customized Leaf (under Locked) | Customized (standalone) | Default Perms (as grantee) | ProjectLeader |
|------|-------------------|--------|-------------------------------|------------------------|---------------------------|---------------|
| TableauTest01 | ✓ (owner + defaults) | ✓ (Operations defaults) | ✓ (Finance Models, Compensation content) | — | ✓ (Corporate + Operations defaults) | — |
| TableauTest02 | — | ✓ (owner + defaults) | ✓ (APAC/Partners content, Finance/Models) | ✓ (Analytics owner) | ✓ (Sales defaults) | — |
| TableauTest03 | — | ✓ (Sales defaults) | ✓ (NA/Reps content — manager on all 3) | ✓ (Analytics content) | ✓ (Sales/NA, EMEA, APAC) | — |
| TableauTest11 | — | — | ✓ (Finance Models DS + workbook owner) | ✓ (Analytics owner) | — | ✓ (Analytics) |
| TableauTest05 | ✓ (via Finance Team) | ✓ (via Sales Team) | ✓ (NA/Reps rep, APAC/Partners, Compensation) | ✓ (direct + group) | ✓ (inherited via groups) | — |
| TableauTest06 | ✓ (via Finance Team) | — | ✓ (NA/Reps rep, APAC/Partners rep) | ✓ (Sandbox content) | — | — |
| TableauTest07 | — | — | ✓ (NA/Reps: Garcia, Partners: Deny) | ✓ (KPI Dashboard, Prototype) | ✓ (via Data Consumers) | — |
| TableauTest08 | — | — | — | — | ✓ (via Data Consumers only) | — |

### What Each Permission Model Tests

| Model | Behavior During Migration | Key Assertions |
|-------|--------------------------|----------------|
| **Locked & Inherited** | Default permissions on `Corporate` project are the only access lever. Cloning defaults at the top project gives the new user identical access to ALL descendants — no child can override. | New user's report shows default_permission_count matching originals. All Corporate child projects governed by same defaults. |
| **Locked (not inherited)** | Default permissions are set per project independently (`Operations`, `Sales`, `Sales/NA`, `Sales/EMEA`, `Sales/APAC`). Each project may grant different users/groups. Clone must replicate defaults on each separately. Customized children (`NA/Reps`, `APAC/Partners`, `Finance Models`, `Compensation`) are excluded — they use explicit per-asset perms. | Each project's defaults are cloned independently. Operations/Compliance inherits Operations' defaults (opted into L&I). New user appears in the correct per-project default rules. |
| **Customized Leaf (under Locked parent)** | Each individual asset (workbook, datasource, flow, VC) has its own explicit permission set — siblings within the same project can have completely different grantees and capabilities. The tool must clone each per-asset permission independently. | Every explicit rule on every asset in the leaf is cloned. Two workbooks in `NA/Reps` have different users granted access. `Exec Comp Detail` has Read:Deny for projlead while `Comp Benchmarks` has Read:Allow for the same user. |
| **Customized (standalone)** | Permissions are set per content item (workbook, datasource). The project itself may have a ProjectLeader grant. Clone replicates each explicit permission rule. | Each explicit rule (52+ total) is cloned for the new user where the old user is the grantee. Group-based perms are handled via group membership clone. |

---

## 19. Verification Checklist

After setting up the environment, run `python validate_setup.py` and confirm:

| Check | Expected |
|-------|----------|
| API authentication | JWT or PAT succeeds |
| All 10 pre-existing users found | ✓ |
| Target users (11–18) do NOT exist | ✓ |
| All 23 projects accessible | ✓ |
| Project permission models correct | 5 Locked & Inherited (Corporate + 4 children) + 1 voluntary L&I (Operations/Compliance), 8 Locked not inherited, 9 Customized |
| All 7 groups found | ✓ |
| All 30 workbooks found | ✓ |
| All 11 datasources found | ✓ |
| All 6 flows found | ✓ |
| All 3 VCs found | ✓ |
| Explicit permissions | 52+ rules |
| Default permissions | 20+ rules |
| Favorites set | 18 total |
| Subscriptions created | 8 total |
| Data alerts created | 6 total |
| Custom views created | 8 total |
| Collections created | 5 total |
| Webhooks created | 4 total |
| Pulse definitions/subscriptions | 4 defs, 8+ subs |
| Tabbed workbooks exist | 4 workbooks with tabs (Corporate Revenue, Budget vs Actual, Compliance Dashboard, Sales Forecasting, KPI Dashboard) |
| Customized leaf content has divergent perms | Verify `Rep: Jones` vs `Rep: Smith` vs `Rep: Garcia` all have different grantees |

---

## 20. Test Execution Order

### Phase 1: Baseline
1. **`--mode dry-run`** → Verify all 8 users classified correctly per tier, per-user reports contain expected counts

### Phase 2: Low-Complexity Clone
2. **`--mode clone --yes`** (CSV with only `TableauTest08`) → Verify low-tier clone (group membership only, zero explicit perms)

### Phase 3: Moderate Clone
3. **`--mode clone --yes`** (CSV with `TableauTest07`, `TableauTest05`) → Verify moderate-tier with explicit perms + favorites + subscriptions

### Phase 4: High-Complexity Full Migrate
4. **`--mode migrate --yes`** (CSV with `TableauTest01`) → Verify very-high-tier: locked-inherited defaults, ownership transfer, alerts, custom views, collections, pulse, webhooks
5. **`--mode migrate --yes`** (CSV with `TableauTest03`) → Verify high-tier: 11+ owned items, locked project defaults, ownership transfer

### Phase 5: Comparison
6. **`--mode dry-run --compare-latest`** → Verify comparison shows `fully_migrated` for completed users, correct classification for remaining

### Phase 6: Clean-Only
7. **`--mode clean-only --yes`** (remaining users) → Verify all access stripped + deactivation

### Phase 7: Resume/Checkpoint
8. **`--resume-latest`** → Interrupt a migrate mid-run (e.g., kill after step 5), then resume to verify checkpoint recovery

### Phase 8: Case-Sensitivity
9. **`--mode dry-run`** (using `user_mappings_case_test.csv`) → Verify case-insensitive matching finds the users

---

## 21. Edge Cases to Verify

| Scenario | How to Create | What It Tests |
|----------|---------------|---------------|
| User in "All Users" group | Automatic for all users | Group skip logic |
| Content in Personal Space | `Personal Notebook` workbook for producer | Path resolution → "Personal Space/" |
| Workbook with 3+ tabbed views | `Compliance Dashboard` (4 tabs) | Permission scan skips tabbed view perms |
| Deny permission | `ExportData:Deny` on KPI Dashboard for viewer1 | Deny mode cloning |
| Connect:Deny | `Connect:Deny` on Analytics Warehouse DS for explorer | Deny on datasource capability |
| Custom view set as default | 5 custom views marked default | Default user status migration |
| Custom view NOT default | 3 custom views without default | No default migration attempted |
| Collection with 4 items | `Corporate Executive Pack` | Full clone-and-replace with max items |
| Private collection (no perms) | `Bookmarks` (explorer) | Clone-and-replace without permission step |
| Alert ownership transfer retry | Occurs naturally under load | Retry with backoff logic |
| 409 conflict on clone | Re-run clone after partial completion | Idempotent skip behavior |
| Case-insensitive match | Mixed-case CSV variant | Lowercase normalization |
| User with zero explicit perms | `TableauTest08` — only group membership | Low-tier, zero explicit perms |
| User with zero favorites | `TableauTest08` | Empty favorites list handling |
| User with zero owned content | `TableauTest07`, `TableauTest08` | Low-tier classification, no ownership transfer |
| ProjectLeader grant | `TableauTest11` on Analytics project | Project-level permission cloning |
| ExplorerCanPublish role | `TableauTest06` | Site role preservation on user creation |
| SiteAdministratorCreator | `TableauTest01` | Admin role preservation |
| Locked project — no content-level perms | Sales sub-projects | Verifies no explicit perms exist to clone (only defaults) |
| Inherited defaults — single set point | Corporate hierarchy | One default set, inheriting children get it |
| Customized leaf breaks inheritance | Operations/Finance Models | Content here has explicit perms, NOT inherited Operations defaults |
| Sibling assets with divergent perms | Rep: Jones vs Rep: Smith vs Rep: Garcia | Same project, 3 workbooks, 3 completely different permission sets |
| Read:Deny on customized leaf | Exec Comp Detail → projlead | Tests explicit Deny on Read capability |
| Connect:Deny on customized leaf | Model Inputs DS → Finance Team | Group-level Deny within a customized leaf |
| User has Allow on one sibling, Deny on another | TableauTest07: Garcia=Read:Allow, Partner:GlobalCo=Read:Deny | Same user, same tier of project, opposite permission modes |
| Mixed parent model: user has defaults AND explicit | TableauTest05 in Operations (via group defaults) AND in Finance Models (explicit) | Both default + explicit perms clone for same user |
| Child opts into Locked & Inherited | Operations/Compliance under Locked (not inherited) parent | L&I child inherits parent defaults voluntarily |
| Three permission models under one parent | Operations has Customized + Customized + L&I children | Siblings with different models coexist |
| Multiple collections with perms | govadmin, projlead, producer collections | Tests multiple clone-and-replace in sequence |
| User in 4+ groups | TableauTest05 (Finance, Sales, Analytics, Data Consumers) | Heavy group cloning |
| User with 2+ alerts | TableauTest01 (Revenue Below Target, Budget Overrun) | Multiple alert ownership transfers |
| Workbook without dashboard | `Valuation Model` — single sheet only | Favorite/subscription on sheet view |
| Deep project nesting (3 levels) | Corporate/Finance/Reports | Path resolution walks parentProjectId chain 3 deep |
| 4-level nesting | Sales/North America/Reps (under Sales/NA under Sales) | Deepest path resolution |

---

## 22. Asset Summary

| Category | Count |
|----------|-------|
| Users (pre-existing) | 10 |
| Users (created by tool) | 8 |
| Projects | 23 |
| Groups (custom) | 7 |
| Workbooks | 30 |
| Views (sheets/dashboards) | ~68 |
| Published Datasources | 11 |
| Flows | 6 |
| Virtual Connections | 3 |
| Explicit Permission Rules | 52+ |
| Default Permission Rules | 26+ |
| Favorites | 18 |
| Subscriptions | 8 |
| Data Alerts | 6 |
| Custom Views | 8 |
| Collections | 5 |
| Webhooks | 4 |
| Pulse Definitions | 4 |
| Pulse Subscriptions | 8+ |
| **Total distinct assets** | **~270+** |

---

## 23. User Classification Expected Results (Dry-Run)

| User | Expected Tier | Key Signals |
|------|--------------|-------------|
| TableauTest01 | **very_high** | Owns projects (Corporate + children), has default permissions |
| TableauTest02 | **very_high** | Owns projects (Sales, Analytics), has default permissions |
| TableauTest03 | **high** | Owns 11 content items (workbooks + datasources), owns published DS |
| TableauTest11 | **high** | Owns flows + VCs + workbooks, significant content producer |
| TableauTest05 | **moderate** | Owns 2 workbooks + 1 DS, has 4 favorites + 1 custom view + 1 subscription |
| TableauTest06 | **moderate** | Owns 1 workbook + 1 datasource, has custom view + subscription + alert |
| TableauTest07 | **low** | No owned content, 2 explicit perms, 1 favorite |
| TableauTest08 | **low** | No owned content, no explicit perms, no favorites — pure group consumer |
