# Plan: Five Enhancement Features (No Code Yet)

This document nails down **how** to implement each of the five enhancements. No code changes—just a concrete plan so we can build them in order or in parallel later.

---

## Current behaviour (baseline)

- **CSV:** Columns `Part_Num` (product identifier / name) and `Trade_Cost` (unit cost).
- **Jobber:** We query `productOrServices` for `id` and `name` only. We match CSV `Part_Num` to Jobber `name` **exactly** (after strip). We never create products; we only call `productsAndServicesEdit` to set `internalUnitCost`.
- **Flow:** Upload CSV → Sync now → result: "Updated N", list of `skus_not_found`, optional error. No preview, no options.

---

## 1. Duplicate protection (match by SKU or name; update only)

### Goal

- Keep the rule: **only update existing products, never create new rows.**
- Optionally match by **SKU/code** when Jobber exposes it, with **name** as fallback so we still work for accounts that don’t use SKU.

### Current state

- We already **only update** (no new rows).
- We match by **name** only; we do not read or match by SKU.

### What we need from Jobber

- **Schema check:** Does `productOrServices` (or the product node) expose a field like `code`, `sku`, or `partNumber`? If yes, we add it to the query and use it for matching. If no, we keep name-only and document that.

### Implementation plan

1. **Schema discovery**
   - In Jobber’s GraphiQL / docs, inspect the type returned by `productOrServices.nodes` (e.g. `ProductOrService`). Note the exact field name for “SKU” or “code” (e.g. `code`, `sku`, `partNumber`).
   - If there is no such field, **skip SKU matching** and only document “Match by product name”.

2. **Backend (sync layer)**
   - **Query:** Extend `GetProductsPage` (or equivalent) to select the SKU/code field in addition to `id` and `name`. Example: `nodes { id name code }` (replace `code` with actual field name).
   - **Matching order (when SKU exists):**
     - For each CSV row `(Part_Num, Trade_Cost)`:
       - First try: find a Jobber product where `product.code == Part_Num` (or equivalent). If found, use that `id` and update cost.
       - Fallback: if no SKU match, find product where `product.name == Part_Num` (current behaviour).
     - If neither matches, add to `skus_not_found`.
   - **When Jobber has no SKU field:** Keep current behaviour (name-only). No code path for “match by SKU first”.

3. **UI / config**
   - Optional: setting or checkbox “Match by SKU first (then name)”. Default on if we have a SKU field; hide or default off if we don’t. No change to CSV format: `Part_Num` is still the column that holds either SKU or name.

4. **Edge cases**
   - Multiple products with same name: we currently take the first match when paginating. Document this. If we add SKU, same rule: first SKU match wins.
   - Empty or null SKU in Jobber: treat as “no SKU match”, fall back to name.

5. **Deliverables**
   - Updated GraphQL query (with optional SKU field).
   - One matching function: `(csv_part_num, jobber_products_list) -> product_id | None` with logic “SKU match else name match”.
   - Tests: match by SKU when present, fallback to name, and name-only when SKU not in schema.

---

## 2. Price protection (only update if new cost is higher)

### Goal

- **Option:** “Only update cost when the new cost is **greater than** the current cost in Jobber.” So we never overwrite with a lower number (protects against accidental drops).

### Current state

- We always set `internalUnitCost` to the CSV value. We never read the current cost from Jobber.

### What we need from Jobber

- **Query:** The product node must expose current cost. Likely `internalUnitCost` (or similar) on the same type we get from `productOrServices`. Confirm in schema: e.g. `nodes { id name internalUnitCost }`.

### Implementation plan

1. **Schema discovery**
   - In the `productOrServices` node type, find the field that holds the current unit cost (e.g. `internalUnitCost`). Add it to the query only when “price protection” is enabled, or always fetch it and ignore when feature is off.

2. **Backend**
   - **Option A – Fetch current cost during sync:** When building the list of Jobber products (for matching), also fetch `internalUnitCost`. When we have a match and are about to call `productsAndServicesEdit`, compare: if `new_cost <= current_cost`, skip the mutation and optionally count as “skipped (protected)”.
   - **Option B – Separate “get current costs” pass:** One pass: for each CSV row, resolve product id (and current cost). Second pass: for each row where `new_cost > current_cost`, run the update. Option A is simpler if we already load products with cost in one place.
   - **Result shape:** Extend sync result to include e.g. `skipped_protected: int` (count of rows we did not update because new cost was not higher). Return these in the API so the UI can show “Updated N, skipped Y (price protection)”.

3. **UI**
   - **Setting:** Checkbox or toggle: “Only update when new cost is higher than current cost” (default: off for backward compatibility). Stored in session, or in a simple per-account preference if we add preferences later.
   - **Results:** Show “Skipped (price protection): Y” when Y > 0.

4. **Edge cases**
   - Current cost is null or zero in Jobber: treat as 0; any positive new cost is “higher”, so we update.
   - Equality: “only if higher” means we skip when `new_cost == current_cost`. If we want “only if higher or equal”, make the condition `new_cost >= current_cost` and document it.

5. **Deliverables**
   - Query change to include current cost when needed.
   - Sync logic: compare before update; skip and count when condition not met.
   - API/result: `skipped_protected` (or similar) in the JSON response.
   - UI: one option + result line. Tests for “update when higher”, “skip when lower”, “skip when equal” (if we choose strict “higher only”).

---

## 3. Audit log / preview (“You are about to increase x and decrease y”)

### Goal

- **Preview step:** Before applying changes, show a summary such as: “You are about to **increase** N prices and **decrease** M prices. Z unchanged. Confirm to apply.”
- User can then **Confirm** (run the real sync) or **Cancel**.

### Current state

- No preview. Upload → Sync now → done.

### What we need from Jobber

- Same as price protection: we need **current** `internalUnitCost` (or equivalent) for each product we might update, so we can compare CSV cost vs current cost and classify as increase / decrease / unchanged.

### Implementation plan

1. **Backend – preview/dry-run**
   - **New endpoint or mode:** e.g. `POST /api/sync/preview` (or `POST /api/sync` with `?preview=true` or body `{"preview": true}`). Same input: CSV file (and account from session).
   - **Logic:**
     - Parse CSV (same as now).
     - For each row, resolve product (same matching as sync: name, or SKU then name when we have it). If not found, add to `skus_not_found`.
     - For each **found** product, we need current cost. So either:
       - Fetch products with `internalUnitCost` in one pass and build a map `product_id -> current_cost`, or
       - When we add “price protection”, we already fetch current cost; reuse that path.
     - Compare CSV cost to current cost: `increase`, `decrease`, or `unchanged`. Count each.
   - **Response (no writes):** e.g. `{ "increases": 12, "decreases": 3, "unchanged": 5, "skus_not_found": ["..."], "error": null }`. No DB or Jobber mutations.

2. **Backend – apply**
   - Keep existing `POST /api/sync`: when not in preview mode, run the real sync (with optional price protection and other options). No change to contract except we may add `skipped_protected` (see feature 2).

3. **UI flow**
   - **Step 1:** User uploads CSV and clicks e.g. **“Preview”** (or “See what will change”).
   - **Step 2:** Call preview API. Show: “You are about to **increase** X prices, **decrease** Y prices, leave Z unchanged. N products not found in Jobber.”
   - **Step 3:** Buttons: **“Cancel”** (back to upload) and **“Apply changes”** (send same file to `POST /api/sync` without preview, then show existing “Updated N” + optional “Skipped Y”).
   - **Optional:** Store nothing; preview is stateless. Or store the last preview in session so “Apply” doesn’t require re-upload (simpler: re-upload on Apply is acceptable for v1).

4. **Edge cases**
   - Very large CSV: preview still does one full product fetch (with current costs). Same rate limits as sync. Consider a limit (e.g. “Preview supports up to 500 rows”) and show a message if over.
   - All not found: show “N products not found; nothing to update.” and no “Apply” or grey it out.

5. **Deliverables**
   - Preview API (no writes).
   - Shared helper: “resolve CSV rows to product ids + current costs” (used by both preview and, when we have it, price protection).
   - UI: Preview button, preview result panel, Confirm/Cancel, then call sync on Confirm. Tests: preview returns correct counts; apply after preview does the right updates.

---

## 4. Fuzzy matching (“Copper Pipe 1/2in” vs “1/2 Copper Pipe”)

### Goal

- When **exact** match fails, optionally try **fuzzy** match so small wording differences still match one Jobber product (e.g. “Copper Pipe 1/2in” ↔ “1/2 Copper Pipe”).

### Current state

- Match is exact: `Jobber name.strip() == Part_Num.strip()`.

### Implementation plan

1. **Strategy (no new API)**
   - We already paginate and get all products (or we build a local list for the current sync). So fuzzy matching is entirely in our code: we have a list of `(id, name)` (and maybe SKU). For each CSV row, we first try exact match; if none, we run a **similarity score** over product names and pick the best match if it’s above a threshold.

2. **Normalization (recommended first step)**
   - Normalize both CSV `Part_Num` and Jobber `name`: lowercase, collapse whitespace to single space, optional: remove punctuation. Compare normalized strings first; if equal, treat as match (catches “1/2 Copper Pipe” vs “1/2  Copper Pipe”). This is cheap and often fixes “spacing/quote” differences.

3. **Similarity (when normalized still doesn’t match)**
   - Use a simple, built-in-friendly method so we don’t require heavy deps. Options:
     - **Python `difflib.SequenceMatcher.ratio()`** on normalized strings: threshold e.g. 0.85 or 0.9. If exactly one product is above threshold, use it; if tie or none, treat as not found (or “ambiguous”).
     - **Token sort:** split on spaces, sort tokens, rejoin, then compare (or ratio on token-sorted strings). Helps “Copper Pipe 1/2in” vs “1/2 Copper Pipe”.
   - **Threshold:** Configurable (e.g. 0.85). Above threshold and single best match → auto-match. Below or multiple above → do not match (or surface as “needs review” in a future phase).

4. **Backend**
   - **Matching function:** `find_product_id(csv_part_num, products_list, exact_only: bool, fuzzy_threshold: float) -> product_id | None`.
     - If `exact_only` or no fuzzy: current behaviour.
     - Else: try exact (normalized); then if no hit, score all names vs `csv_part_num`, take max; if max >= threshold and no tie, return that product’s id.
   - **Performance:** We’re already iterating products per row (or loading a full list). Fuzzy adds O(n) comparisons per row; n = product count. For thousands of products and hundreds of rows, still acceptable in a single request; if needed, we can cache the product list for the request.

5. **UI**
   - **Setting:** “Fuzzy matching” checkbox (default off). Optional: “Fuzzy sensitivity” slider or dropdown (e.g. 0.8 / 0.9 / 0.95). Stored like price protection (session or simple preference).
   - **Results:** When we used a fuzzy match, we could add a note per row in a detailed report, or just “Matched with fuzzy: N” in the summary. For v1, summary is enough.

6. **Edge cases**
   - **Ambiguity:** Two products both at 0.9 with the same score: do not match; add to `skus_not_found` (or “ambiguous” list if we add it).
   - **Wrong match:** Fuzzy can mis-match. So default off, and document “use with care; review results.” Optional future: “review queue” for low-confidence matches before applying.
   - **Encoding/special chars:** Normalize to Unicode NFKC and strip control chars so “½” and “1/2” are handled consistently if we ever add character normalization.

7. **Deliverables**
   - Normalize function (lower, collapse space).
   - Fuzzy matcher: score + threshold + tie handling.
   - `find_product_id` with `exact_only` and `fuzzy_threshold` (or off).
   - Optional result field: `fuzzy_matched_count`. UI toggle. Tests: exact unchanged when fuzzy off; fuzzy matches when on and above threshold; no match on tie or below threshold.

---

## 5. Markup calculator (update cost, then set Unit Price = Cost + x%)

### Goal

- User uploads **wholesale cost** CSV (same format: Part_Num, Trade_Cost).
- User sets a **markup** (e.g. “+25%”).
- App: (1) updates **internalUnitCost** from CSV, (2) sets **Unit Price** (selling price) = cost × (1 + markup).

### Current state

- We only set `internalUnitCost`. We never read or write “Unit Price” (selling price).

### What we need from Jobber

- **Schema discovery:** The mutation or type that has `internalUnitCost` likely has a field for selling price—e.g. `unitPrice`, `price`, or similar. We need the **exact field name** and whether it’s in the same `productsAndServicesEdit` input or a different mutation (e.g. “set price”).
   - Check: `productsAndServicesEdit` input type (or the product type) for a price/selling-price field. If it’s the same mutation, we send both `internalUnitCost` and `unitPrice` (or whatever it’s called) in one call. If separate mutation, we do cost update then price update (two calls per product, more rate-limit impact).

### Implementation plan

1. **Schema discovery**
   - Find the field for “unit price” / “selling price” on the product or in the edit mutation input. Note type (e.g. Float, Money). Confirm whether we can set it in the same `productsAndServicesEdit` call as `internalUnitCost` (preferred) or need a second mutation.

2. **Backend**
   - **Input:** Markup percentage from the user. Stored in request body or form: e.g. `markup_percent: 25` means “Unit Price = Cost × 1.25”.
   - **Formula:** For each row: `cost = Trade_Cost` from CSV. `unit_price = cost * (1 + markup_percent / 100)`. Round to 2 decimals (or Jobber’s required precision).
   - **Mutation:** If Jobber accepts both in one call: one mutation with `internalUnitCost: cost` and `unitPrice: unit_price` (or the actual field name). If two mutations: first update cost, then update price (same product id); respect rate limits.
   - **Optional:** “Only set unit price when updating cost” (i.e. if price protection skips the cost update, we might skip the price update for that row too). Decide and document.

3. **UI**
   - **Input:** “Markup %” number input or slider (e.g. 0–100). Optional: “Apply markup to set Unit Price” checkbox (when unchecked, we only update cost as today).
   - **Results:** “Updated N costs and unit prices” or “Updated N costs (markup not applied)” if markup was 0 or off. No need to show per-row price in the first version.

4. **Edge cases**
   - Markup 0%: unit price = cost. Allowed.
   - Jobber has tax-inclusive vs exclusive: we assume the field we set is the one Jobber uses for “unit price” as shown in the product; document that we don’t handle tax calculation.
   - Very large markup: no cap in code; UI could warn above e.g. 500% if desired.

5. **Deliverables**
   - GraphQL: add the unit-price field to the mutation (or add a second mutation and call it after cost).
   - Sync: for each row, compute `unit_price`; call mutation(s) with cost and unit price.
   - API: accept `markup_percent` (optional, default 0 or “off”). UI: markup input + checkbox. Tests: cost and unit price both set when markup provided; cost-only when markup off.

---

## Implementation order and dependencies

| Feature              | Depends on              | Suggested order |
|----------------------|-------------------------|------------------|
| 1. SKU / name match  | Jobber schema (SKU)     | First (small query + match change) |
| 2. Price protection  | Current cost in query   | After 1 or in parallel with 3 |
| 3. Preview / audit   | Current cost in query   | Same as 2 (shared “current cost” fetch) |
| 4. Fuzzy matching   | None                    | Anytime; keep optional and off by default |
| 5. Markup calculator | Jobber unit-price field | After schema check; independent of 2/3 |

**Suggested sequence**

1. **Schema discovery (one-off):** Check Jobber GraphQL for: (a) product SKU/code field, (b) product `internalUnitCost` in query, (c) product unit price / selling price field and which mutation sets it. Document in this repo (e.g. in this file or a `JOBBER_SCHEMA_NOTES.md`).

2. **1 – Duplicate protection (SKU):** Extend query; add SKU-first then name fallback; tests.

3. **2 + 3 – Price protection and Preview:** Extend query to include current cost. Implement preview API (dry-run with counts). Implement “only update if new > old” and `skipped_protected`. UI: preview button + summary + confirm; price protection checkbox; result lines.

4. **4 – Fuzzy matching:** Normalize + difflib (or token sort); optional toggle; tests; document “use with care”.

5. **5 – Markup:** Add unit price to mutation (or second mutation); accept `markup_percent`; compute and set unit price; UI input; tests.

---

## Summary table

| # | Feature            | New API/query?              | New UI?                    | New result fields?           |
|---|--------------------|-----------------------------|----------------------------|------------------------------|
| 1 | SKU / name match   | Query: add SKU field        | Optional “match by SKU”    | No                           |
| 2 | Price protection   | Query: current cost         | Checkbox “only if higher”  | `skipped_protected`          |
| 3 | Preview / audit    | Same as 2 (current cost)    | Preview + Confirm/Cancel   | Preview: increases/decreases/unchanged |
| 4 | Fuzzy matching     | No                          | Checkbox “fuzzy match”     | Optional `fuzzy_matched_count` |
| 5 | Markup calculator  | Mutation: unit price field   | Markup % input            | No (or “prices_updated”)     |

All of this can be implemented incrementally; each feature can be toggled or default-off so existing behaviour stays the default until you enable the new options.
