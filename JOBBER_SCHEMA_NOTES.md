# Jobber GraphQL schema notes

This file documents schema assumptions used by the Price Sync app. Verify in [GraphiQL](https://developer.getjobber.com/docs/getting_started/) (Manage Apps → Test in GraphiQL → Documentation) for your API version.

## Product / Service

- **Query:** `productOrServices(first, after)` returns a connection with `nodes` (or `edges { node }`), `pageInfo { hasNextPage, endCursor }`.
- **Node fields used:** `id`, `name`, `internalUnitCost`. With Enhancement 1: `code` (SKU) when available.
- **Mutation:** `productsAndServicesEdit(productOrServiceId: EncodedId!, input: { ... })` returns `{ productOrService { id }, userErrors [] }`.

### Cost

- **internalUnitCost** (Float): Unit cost. We set this from the CSV `Trade_Cost`.

### Unit price (selling price) – Enhancement 5

- **Assumption:** The same `productsAndServicesEdit` input accepts a field for the selling/unit price. We use **`unitPrice`** (Float).
- If your schema uses a different name (e.g. `price`, `sellingPrice`) or a separate mutation, update `app/sync.py`: `MUTATION_UPDATE_COST_AND_PRICE` and the variables passed to the mutation.
- We send both `internalUnitCost` and `unitPrice` in one call when markup % is set; formula: `unitPrice = cost * (1 + markup_percent/100)`, rounded to 2 decimals.

## Version

- **X-JOBBER-GRAPHQL-VERSION** used: `2026-02-17` (see `app/sync.py`). Newer versions may add or rename fields; check the schema before upgrading.
