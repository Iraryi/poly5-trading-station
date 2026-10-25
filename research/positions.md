> ## Documentation Index
> Fetch the complete documentation index at: https://docs.polymarket.com/llms.txt
> Use this file to discover all available pages before exploring further.

# List positions for a user or market

> A keyset page of positions in the standard
`{ data, pagination }` envelope. One route serves a user's open book, their
closed book (`status=CLOSED`), and a market's holders (market anchor).

At least one of `user`/`condition` is required. Supplying both anchors on the
user and applies `condition` as a narrowing filter.

`status` defaults to `OPEN`, which is the **superset**: a
settled-but-unredeemed winner still holds its tokens, so it is an open
position whose condition resolved. `REDEEMABLE` narrows to that subset, and
each row echoes its own `status`.

Resume contract: a `next_cursor` binds the `status`/`sortBy`/`sortDirection`
the walk started with. To follow it, send `cursor` together with the same
`user` or `condition` anchor used on the first page; a bare `?cursor=` request
returns 400. The token is the authority on the spine and the total order,
and the handler adopts all three from it. Restating them is allowed and
must agree;
**explicitly** contradicting one is a 400, because replaying an anchor under
a different spine or order would silently walk a different result set.
Omitting them is not a contradiction; a defaulted `status` is the absence of
a choice, not a request for `OPEN`.
User CLOSED residual sorts bind their realized-PnL ranking in the signed
cursor kind. Earlier cursors for those sorts return 400 and require restarting
pagination without `cursor`, because their numeric anchor has ambiguous units.

A malformed `condition` id is a `400` naming the value; a
well-formed one that matches nothing serves an empty `data` array, which
is the meaningful zero-state; absence means the market is not servable,
and only that.



## OpenAPI

````yaml https://data-api.polymarket.com/v2/openapi.json get /v2/positions
openapi: 3.1.0
info:
  title: Polymarket Data API v2
  description: >-
    The Polymarket Data API: wallet portfolios, trade and activity feeds, market
    state and ranked boards.


    ## Conventions every endpoint shares


    - **Envelope**: every response wraps its payload in `data` (paged routes add
    `pagination`). A documented miss is `data: null` or an empty list, never an
    error.

    - **Pagination is cursor-only**: follow `pagination.next_cursor` until
    `null`; `has_more` is exact, and there is no `offset` query parameter
    (sending one is a `400`). Cursors are signed, typed per endpoint, and
    opaque. The feeds (`trades`, `activity`, combo activity) are keyset walks,
    stable across concurrent writes; the boards, `holders` and most
    combo-position sorts are offset walks behind the opaque token, so a page
    taken across a data refresh can skip or repeat rows. Where a cursor binds
    its cohort (the boards, positions, combo positions), resuming bare is fine,
    restating the same values is fine, and contradicting them is a `400`. The
    `trades`/`activity` feed cursors carry only the seek anchor and page size
    (plus the sort direction on activity): re-send identical filters on every
    page, because changing one mid-walk re-anchors silently.

    - **Rate limiting**: `429` with `Retry-After` is the busy signal for heavy
    queries. A heavy query may first be queued briefly for a capacity slot; the
    `429` arrives only if that short wait ends unserved. Each caller also has a
    per-client request allowance, and bursts past it get the same `429` with
    `Retry-After` sized to the remaining wait. Retry after the given delay. A
    request that could not get a database connection within its budget is NOT a
    `429`: it is a `503` `request_timeout` with `Retry-After`, because the
    shortage is on the server side, not in the caller's rate.

    - **Identifiers**: `condition` (aliases `condition_id`, `conditionId`) is
    the unified query key for on-chain 0x condition ids; `market_id` fields
    carry Gamma's own market ids; `event_id` takes Gamma event ids; `token_id`
    is the CLOB asset id (the key on `/v2/prices-history`).

    - **Params** accept both snake_case and camelCase spellings.

    - **Units**: bare `volume`/`size` values are **shares**; `_usdc` suffixed
    fields are USD; `taker_` prefixed volumes are one-side.

    - **Sentinels**: `outcome_index: 999` means the outcome could not be
    labeled; a missing or `null` numeric field means unavailable, never zero.

    - **Windows on `/v2/trades?user=` and `/v2/activity`**: an omitted or `0`
    `start` floors to three years back (`start=1` asks for full history); an
    omitted or `0` `end` is now plus one day. The other `/v2/trades` shapes
    ignore `start`/`end`: `condition`/`event_id` serve a fixed three-year window
    and the bare feed serves the rolling current-plus-previous month. Other
    windowed routes treat omitted/`0` bounds as unbounded; each documents its
    own rule. `/v2/prices-history` is the strict one, where a `0` bound is a
    `400`.

    - **Errors**: every unsuccessful response is JSON with a human-readable
    `error`, stable `code`, `retryable` flag, and opaque `trace_id`; validation
    failures may also name `parameter`. Codes map to statuses as follows:
    `invalid_request` = `400`, `not_found` = `404`, `method_not_allowed` =
    `405`, `rate_limited` = `429`, `internal` = `500`, and both
    `request_timeout` (the request deadline, the datastore's statement timeout,
    or the connection pool's acquire budget) and `dependency_unavailable` =
    `503`. A `429` or `503` that is worth retrying carries `Retry-After` in
    seconds. Every response, successful or not, echoes the same id in the
    `x-trace-id` header; supply it when reporting a failure so operators can
    correlate it with telemetry.

    - **Auth**: none. All data routes are public; no API key or token is
    required.
  contact:
    name: Polymarket
  license:
    name: MIT
    identifier: MIT
  version: 0.1.0
servers:
  - url: https://data-api.polymarket.com
    description: Production
  - url: https://data-api-rs.stage.pmd.use1.polymarket.sh
    description: Staging
security: []
tags:
  - name: wallet
    description: >-
      Everything anchored on one wallet: positions (base and combos), portfolio
      value, PnL history, the profile card, trading volume, and token approvals.
      Pass the proxy wallet as `user`. One exception cuts across sections:
      `/v2/positions` with `condition` alone (no `user`) answers the market-wide
      holders question.
  - name: feeds
    description: >-
      The high-traffic keyset feeds: trades, activity and combo activity. Filter
      by `user`, `condition` or `event_id`; page with `next_cursor`.
  - name: markets
    description: >-
      Market and event state, keyed by on-chain `condition` ids, Gamma
      `event_id`s or a CLOB `token_id`: open interest, holders, per-event taker
      volume, resolution lifecycle, and price history.
  - name: boards
    description: >-
      Ranked, windowed boards: the PnL/volume leaderboard, biggest single wins,
      and the builder standings and volume buckets. Cursors pin the board they
      were minted on.
  - name: service
    description: >-
      Service metadata: data freshness: the serving watermark, its lag, and
      per-stream ingestion cursors.
paths:
  /v2/positions:
    get:
      tags:
        - wallet
      summary: List positions for a user or market
      description: >-
        A keyset page of positions in the standard

        `{ data, pagination }` envelope. One route serves a user's open book,
        their

        closed book (`status=CLOSED`), and a market's holders (market anchor).


        At least one of `user`/`condition` is required. Supplying both anchors
        on the

        user and applies `condition` as a narrowing filter.


        `status` defaults to `OPEN`, which is the **superset**: a

        settled-but-unredeemed winner still holds its tokens, so it is an open

        position whose condition resolved. `REDEEMABLE` narrows to that subset,
        and

        each row echoes its own `status`.


        Resume contract: a `next_cursor` binds the
        `status`/`sortBy`/`sortDirection`

        the walk started with. To follow it, send `cursor` together with the
        same

        `user` or `condition` anchor used on the first page; a bare `?cursor=`
        request

        returns 400. The token is the authority on the spine and the total
        order,

        and the handler adopts all three from it. Restating them is allowed and

        must agree;

        **explicitly** contradicting one is a 400, because replaying an anchor
        under

        a different spine or order would silently walk a different result set.

        Omitting them is not a contradiction; a defaulted `status` is the
        absence of

        a choice, not a request for `OPEN`.

        User CLOSED residual sorts bind their realized-PnL ranking in the signed

        cursor kind. Earlier cursors for those sorts return 400 and require
        restarting

        pagination without `cursor`, because their numeric anchor has ambiguous
        units.


        A malformed `condition` id is a `400` naming the value; a

        well-formed one that matches nothing serves an empty `data` array, which

        is the meaningful zero-state; absence means the market is not servable,

        and only that.
      operationId: get_positions
      parameters:
        - name: user
          in: query
          description: >-
            The wallet to anchor on. At least one of `user`/`condition` is
            required.
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: condition
          in: query
          description: >-
            Condition id(s), comma-separated (at most 20 distinct values). With

            `user`, narrows that user's positions (all ids honoured). Without

            `user`, anchors on the market's holders; exactly one id is accepted

            there, and a multi-id list is rejected rather than silently
            truncated.
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: limit
          in: query
          description: >-
            First-page size. Ignored when `cursor` is supplied (the cursor's
            size wins).
          required: false
          schema:
            type:
              - integer
              - 'null'
            format: int32
            maximum: 1000
            minimum: 0
        - name: cursor
          in: query
          description: >-
            Opaque pagination cursor from a prior response's `next_cursor`. It

            carries the page position, page size, and the status/sort/direction
            it

            was minted under.
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: status
          in: query
          description: >-
            One of `OPEN`, `REDEEMABLE`, `REDEEMABLE_LOST`, `MERGEABLE`, or
            `CLOSED`;

            defaults to `OPEN`. `REDEEMABLE_LOST` requires `user`, supports all
            six

            sorts, and defaults to `CURRENT_VALUE`; it preserves row status

            `REDEEMABLE`. `MERGEABLE` is `OPEN` narrowed to live complementary
            pairs

            (conditions where the wallet holds >= 2 servable-live tokens) and
            defaults

            to `TOKENS`; it is user-scoped, so a market-anchored request renders
            as

            `OPEN`.


            `OPEN` is the superset; it includes settled-but-unredeemed winners,

            which `REDEEMABLE` narrows to. `CLOSED` is exited positions.
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: event_id
          in: query
          description: |-
            Event id(s), comma-separated (at most 20 distinct values).
            User-anchored only.
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: title
          in: query
          description: |-
            Case-insensitive market-title substring filter, honoured on every
            anchor and status. SQL LIKE wildcards (`%`, `_`) keep their usual
            meaning; empty or whitespace-only is treated as absent; at most 200
            characters. NOT carried by the cursor: re-send it on every page of a
            walk (like `condition` and the `start`/`end` window), or the cohort
            silently widens.
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: filter_type
          in: query
          description: |-
            `CASH` or `TOKENS`; defaults to `TOKENS` (the /v2/trades-homogenized
            filter pair, replacing the former `size_threshold`).
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: filter_amount
          in: query
          description: |-
            The filter floor. `TOKENS`: minimum CURRENT holding in shares
            (defaults to the 0.1 dust floor; applies to `OPEN`/`REDEEMABLE`; a
            user's `CLOSED` set is not narrowed by it, and on a market anchor it
            moves the OPEN/CLOSED boundary). `CASH`: minimum mark-to-market
            `current_value` in USDC, on top of the token dust floor. Invalid
            values fall back to the default.
          required: false
          schema:
            type:
              - number
              - 'null'
            format: double
        - name: include_archived
          in: query
          description: >-
            Also include positions on archived markets; defaults to `false`.

            `OPEN`/`REDEEMABLE`/`REDEEMABLE_LOST`/`MERGEABLE` only; combining it
            with

            `status=CLOSED` is rejected. Inactive markets remain excluded either
            way.
          required: false
          schema:
            type:
              - boolean
              - 'null'
        - name: sort_by
          in: query
          description: >-
            One of `CURRENT_VALUE`, `PRICE`, `TOKENS`, `UNREALIZED_PNL`,

            `REALIZED_PNL`, `TOTAL_PNL`, or `TIMESTAMP` (the row's
            `last_event_at`).

            `PRICE` ranks by the response's effective `current_price` on every

            supported anchor and status. On `REDEEMABLE`, the existing
            winners-first

            partition remains ahead of the requested price order. The default

            follows the status: `CURRENT_VALUE` for `OPEN`/`REDEEMABLE`,

            `REALIZED_PNL` for `CLOSED`. On a user's `CLOSED` positions,
            `TOKENS`,

            `CURRENT_VALUE`, `PRICE` and `UNREALIZED_PNL` describe a holding
            that no

            longer exists, so those pages are served in `REALIZED_PNL` order.
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: start
          in: query
          description: >-
            Inclusive lower bound on `last_event_at`, epoch seconds; omit or `0`
            for

            unbounded (the `/v2/activity` + `/v2/trades` vocabulary).
          required: false
          schema:
            type:
              - integer
              - 'null'
            format: int64
        - name: end
          in: query
          description: >-
            Inclusive upper bound on `last_event_at`, epoch seconds; omit or `0`
            for

            unbounded.


            A position with no native economics carries no `last_event_at` and
            is

            therefore **excluded by any bound**, in either direction; a window
            asks

            which positions moved inside it, and a row with no clock has no
            answer.

            Unbounded requests still serve those rows.
          required: false
          schema:
            type:
              - integer
              - 'null'
            format: int64
        - name: sort_direction
          in: query
          description: '`ASC` or `DESC`; defaults to `DESC`.'
          required: false
          schema:
            type:
              - string
              - 'null'
      responses:
        '200':
          description: A page of positions
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PositionsPage'
        '400':
          description: >-
            Missing anchor, invalid query params or cursor, an unknown
            status/sort/direction, a malformed 'condition' id, an over-long
            'title', or a 'user' that is a known protocol contract address
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '429':
          description: Service is at heavy-query capacity; retry after `Retry-After`
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '500':
          description: Internal server error
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '503':
          description: >-
            Timed out (the request deadline, the datastore statement timeout, or
            the connection pool's acquire budget) or a serving dependency is
            unavailable; retry after `Retry-After`
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
components:
  schemas:
    PositionsPage:
      type: object
      description: '`{ data, pagination }` envelope for `/v2/positions`.'
      required:
        - data
        - pagination
      properties:
        data:
          type: array
          items:
            $ref: '#/components/schemas/Position'
          description: The page's rows.
        pagination:
          $ref: '#/components/schemas/Pagination'
          description: 'Paging envelope: follow `next_cursor` until `null`.'
    ErrorResponse:
      type: object
      description: Error body returned by Data API endpoints for unsuccessful requests.
      required:
        - error
        - code
        - retryable
        - trace_id
      properties:
        code:
          $ref: '#/components/schemas/ErrorCode'
          description: Stable classification suitable for programmatic branching.
        error:
          type: string
          description: Human-readable error message.
        parameter:
          type:
            - string
            - 'null'
          description: Query or body parameter associated with a validation failure.
        retryable:
          type: boolean
          description: Whether an automated consumer may retry the request unchanged.
        trace_id:
          type: string
          description: Opaque identifier shared with structured logs and error telemetry.
    Position:
      type: object
      description: >-
        One position (`/v2/positions`); a holding in a single outcome token,
        priced

        and enriched.


        The shape is **uniform across all three arms** (user OPEN/REDEEMABLE,
        user

        CLOSED, market-anchored): clients never branch on which spine answered.
        On

        the CLOSED arm `current_size`/`current_value`/`unrealized_pnl` are ~0 by
        construction, which is exactly

        what a closed position should report.


        Keyset-paginated on the active `sort_by` key: follow the response's

        `next_cursor` to page, and expect the ordering to change with `sort_by`.
      required:
        - proxy_wallet
        - token_id
        - condition_id
        - current_size
        - avg_price
        - entry_cost_usdc
        - entry_fees_usdc
        - total_cost_usdc
        - current_price
        - current_value
        - total_size
        - realized_pnl
        - unrealized_pnl
        - total_pnl
        - percent_pnl
        - percent_realized_pnl
        - status
        - redeemable
        - mergeable
        - negative_risk
        - archived
        - title
        - slug
        - icon
        - event_id
        - event_slug
        - outcome
        - outcome_index
        - opposite_outcome
        - opposite_token_id
        - end_date
        - last_event_at
        - first_entry_at
        - name
        - profile_image
        - verified
      properties:
        archived:
          type: boolean
          description: >-
            Whether the market is archived; tells a caller using
            `includeArchived`

            which rows the flag surfaced.
        avg_price:
          type: number
          format: double
          description: Weighted-average entry price per share, in USDC.
        condition_id:
          type: string
          description: The on-chain condition id.
        current_price:
          type: number
          format: double
          description: Live mark per share, in USDC.
        current_size:
          type: number
          format: double
          description: The CURRENT holding, in shares (~0 residual on the CLOSED arm).
        current_value:
          type: number
          format: double
          description: '`current_size × current_price`, in USDC.'
        end_date:
          type: string
          description: Market end date, `YYYY-MM-DD`; `1970-01-01` when Gamma has none.
        entry_cost_usdc:
          type: number
          format: double
          description: The fee-EXCLUSIVE entry basis.
        entry_fees_usdc:
          type: number
          format: double
          description: |-
            Attributed BUY-fee total for the position. Disclosure only:
            `entry_cost_usdc` is already fee-exclusive, so never re-deduct this
            from a PnL column.
        event_id:
          type: string
          description: Gamma event id of the parent event.
        event_slug:
          type: string
          description: Parent event slug.
        first_entry_at:
          type: integer
          format: int64
          description: >-
            First acquisition of the position (buy, split, mint, transfer in),
            epoch seconds; 0 when no acquisition is recorded.
        icon:
          type: string
          description: Market icon URL.
        last_event_at:
          type: integer
          format: int64
          description: >-
            The row's last economics event, epoch seconds; 0 without native
            state.
        mergeable:
          type: boolean
          description: |-
            Whether the wallet also holds the opposite outcome, so the pair can
            merge back into collateral.
        name:
          type: string
          description: Profile display name of the wallet.
        negative_risk:
          type: boolean
          description: Whether the market belongs to a neg-risk group.
        opposite_outcome:
          type: string
          description: Label of the market's other outcome; what a merge pairs with.
        opposite_token_id:
          type: string
          description: Token id of the market's other outcome.
        outcome:
          type: string
          description: Label of the held outcome (e.g. `Yes`).
        outcome_index:
          type: integer
          format: int32
          description: |-
            Index of the held outcome within the market; `999` means the outcome
            could not be labeled.
        percent_pnl:
          type: number
          format: double
          description: |-
            `(current_value - entry_cost_usdc) / entry_cost_usdc`, as a percent.
            Fee-exclusive basis, and the numerator is `unrealized_pnl`; not
            `total_pnl / total_cost_usdc`.
        percent_realized_pnl:
          type: number
          format: double
          description: >-
            `(current_value - total_size × avg_price) / (total_size ×
            avg_price)`,

            as a percent. A compatibility shape: despite the name, it is not

            `realized_pnl` over a basis.
        profile_image:
          type: string
          description: Profile image URL.
        proxy_wallet:
          type: string
          description: Proxy wallet holding the position.
        realized_pnl:
          type: number
          format: double
          description: Realized PnL in USDC, cumulative for the position.
        redeemable:
          type: boolean
          description: >-
            Whether the position can be redeemed now: its market resolved and
            the

            tokens are still held (losing sides included; redeemable ≠ won).
        slug:
          type: string
          description: Market slug; the URL segment on polymarket.com.
        status:
          type: string
          description: |-
            The row's actual state; can be narrower than the requested `status`,
            since an `OPEN` request also returns `REDEEMABLE` rows.
        title:
          type: string
          description: Market question title (Gamma enrichment; empty when unenriched).
        token_id:
          type: string
          description: The outcome token id.
        total_cost_usdc:
          type: number
          format: double
          description: >-
            Gross (fee-INCLUSIVE) basis. Always exactly

            `entry_cost_usdc + entry_fees_usdc`; the contract sums the two
            served

            columns, so the identity holds on every row of every arm.
        total_pnl:
          type: number
          format: double
          description: Always equals `realized_pnl + unrealized_pnl`.
        total_size:
          type: number
          format: double
          description: |-
            LIFETIME bought shares (the WAC denominator), never the
            current balance; that is `current_size`.
        unrealized_pnl:
          type: number
          format: double
          description: 'Unrealized (mark-to-market) PnL: `current_value - entry_cost_usdc`.'
        verified:
          type: boolean
          description: Profile verification badge.
    Pagination:
      type: object
      required:
        - limit
        - offset
        - has_more
      properties:
        has_more:
          type: boolean
          description: |-
            Exact: `true` iff another page exists; probe-based, never inferred
            from page fullness.
        limit:
          type: integer
          format: int32
          description: Page size this page was served with.
          minimum: 0
        next_cursor:
          type:
            - string
            - 'null'
          description: Opaque, signed cursor for the next page; `null` on the last page.
        offset:
          type: integer
          format: int32
          description: |-
            Running item offset for display continuity across keyset pages (the
            cursor drives the actual seek; this is cosmetic; there is no total).
          minimum: 0
    ErrorCode:
      type: string
      description: Stable machine-readable classification for Data API failures.
      enum:
        - invalid_request
        - unauthorized
        - not_found
        - method_not_allowed
        - request_timeout
        - rate_limited
        - dependency_unavailable
        - internal

````

This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.