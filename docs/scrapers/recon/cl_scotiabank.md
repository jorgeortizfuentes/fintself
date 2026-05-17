# Scotiabank Chile — recon

Sanitized recon notes for the `cl_scotiabank` scraper. The full working notes (with timing observations and screenshots) live under `recon/scotiabank_notes.md` (gitignored).

## Login flow

```
PUBLIC_HOME_URL = "https://www.scotiabankchile.cl/"
PUBLIC_LOGIN_BUTTON = 'text=Acceso Scotia'   # use .first (appears 2-3x)
PUBLIC_LOGIN_PERSONAS = 'role=link[name="Ingreso Personas"]'

# Post-redirect SPA login (URL has CSRF-like token in `?c=...`):
LOGIN_URL_PREFIX = "https://banco.scotiabank.cl/mfe-login/scotia"

RUT_INPUT      = '[data-testid="inputDni"]'   # RUT with dots+dash
PASSWORD_INPUT = '[data-testid="inputPassword"]'
SUBMIT_BUTTON  = 'role=button[name="Ingresar"]'

DASHBOARD_URL_FRAGMENT = "/mfe-home-cl/"
```

⚠️ **DO NOT** use `button:has-text('Cerrar')` for any tour-dismissal heuristic — that string is the **logout** button text on the dashboard. Use the `TOUR_DISMISS_SELECTORS` list in `ScotiabankScraper` instead.

### Tour-dismiss safety

`_dismiss_onboarding_tour` polls the `TOUR_DISMISS_SELECTORS` list (driver.js
close button, `Saltar`, `Omitir`, `Entendido`, `Finalizar tour`, `Listo`, and
scoped `[role='dialog'] button[aria-label='Cerrar']` / `.modal button[aria-label='Cerrar']`).
The bare text `Cerrar` is intentionally absent: the dashboard renders a
**logout** button whose label is `Cerrar sesión`, and a substring match would
log the user out mid-scrape. Any new heuristic added here MUST scope `Cerrar`
to a dialog/modal ancestor or rely on `aria-label="Cerrar"`.

## Dashboard navigation

Top-menu items render as `<span class="menu-column--item__title">` nested inside clickable parents. Reliable click via `page.get_by_text("Cuentas")` / `page.get_by_text("Tarjetas")`.

Dashboard cards expose direct links like `Ver cartola` / `Ver saldos` (`span.StandaloneLinkstyle__Text-canvas-core`).

## Iframe shell

All movement data renders inside `<iframe id="iframe-stage">`. Outer page only hosts the chrome (menu, footer). Use:

```python
frame = page.frame_locator("#iframe-stage")
# or
for f in page.frames:
    if "mfe-accounts-balancesmovements-web" in f.url:
        ...
```

Iframe URLs by section:

| Section            | URL fragment                                           |
|--------------------|--------------------------------------------------------|
| Checking           | `mfe-accounts-balancesmovements-web/?tab=saldos&type=CTACTE` |
| CC (billed/unbilled) | `mfe-simple-account-statement-web-cl/?tab=movimientos-facturados` |

CC billed and unbilled share the same MFE; tabs switch content.

⚠️ The page actually attaches **two** frames whose URL contains the section
fragment: the outer shell wrapper (URL also contains `mfe-shell`) and the
inner content frame (no `mfe-shell` segment). Only the inner frame hosts the
table DOM. `_get_stage_frame` MUST filter out any frame whose URL contains
`mfe-shell` and keep polling until the inner content frame is attached —
returning the shell wrapper produces empty selectors and a misleading
"table did not render" failure.

### Per-tab shell URLs

Each section navigates to a dedicated shell URL so Playwright lands on a
fresh iframe state. Reusing one URL and clicking the tab leaks the previous
pane's DOM (both panes stay mounted) and causes duplicate extractions.

| Constant | URL | Returns |
|----------|-----|---------|
| `CHECKING_SHELL_URL` | `…/mfe-accounts-balancesmovements-web/?tab=saldos&type=CTACTE` | Checking ("Saldos y últimos movimientos") for the auto-selected CTACTE. |
| `CC_BILLED_SHELL_URL` | `…/mfe-simple-account-statement-web-cl/?tab=movimientos-facturados` | CC movements already billed in the current statement, with the Facturados tab pre-active. |
| `CC_UNBILLED_SHELL_URL` | `…/mfe-simple-account-statement-web-cl/?tab=movimientos-no-facturados` | CC movements still pending billing, with the No-facturados tab pre-active. |

All three are built from `SHELL_BASE = "https://www.scotiabank.cl/mfe/sweb/mfe-shell-web-cl/mfe/"`.

## Live extraction flow

End-to-end order followed by `ScotiabankScraper.scrape`, citing the constants
used at each step:

1. **Public navigation** → `HOME_URL` (`https://www.scotiabankchile.cl/`).
2. **Open login** → click first `PUBLIC_LOGIN_TEXT` ("Acceso Scotia"), then
   `PUBLIC_LOGIN_PERSONAS_NAME` ("Ingreso Personas") link.
3. **SPA login form** → wait for `RUT_INPUT`, type RUT and password into
   `RUT_INPUT` / `PASSWORD_INPUT`, click `SUBMIT_BUTTON`. Expect URL to match
   `DASHBOARD_URL_FRAGMENT` (`/mfe-home-cl/`).
4. **Tour dismissal** → `_dismiss_onboarding_tour` polls
   `TOUR_DISMISS_SELECTORS` for up to `POST_LOGIN_SETTLE_MS` (8000 ms).
5. **Checking** → navigate to `CHECKING_SHELL_URL`, run a short
   `TOUR_PRE_NAV_MS` (500 ms) tour sweep, then `_get_stage_frame(CHECKING_IFRAME_URL_FRAGMENT)`
   and wait for `CHECKING_TABLE` (`table.Table__dataTable`). Parse rows with
   `CHECKING_ROW` / `CHECKING_CELL`.
6. **CC billed** → `_scrape_cc_tab(CC_BILLED_SHELL_URL, TAB_BILLED_SELECTOR, "Facturado")`.
7. **CC unbilled** → `_scrape_cc_tab(CC_UNBILLED_SHELL_URL, TAB_UNBILLED_SELECTOR, "NoFacturado")`.

Inside `_scrape_cc_tab` for each CC URL:

1. Navigate to the shell URL, run the pre-nav tour sweep.
2. Resolve the inner content frame with `_get_stage_frame(CC_IFRAME_URL_FRAGMENT)`.
3. Wait for the tab button (`TAB_BILLED_SELECTOR` / `TAB_UNBILLED_SELECTOR`),
   defensively click it (no-op when already active), then wait for `CC_TABLE_NAC`.
4. Extract the card id via `CC_CARD_LABEL_SELECTOR` / `CC_CARD_VALUE_SELECTOR`.
5. **Nacional sub-tab** → `_select_cc_radio(SUBTAB_NACIONAL)` →
   `_expand_all_ver_mas(anchor_selector=CC_TABLE_NAC)` → parse with
   `CC_TABLE_NAC` / `CC_ROW` / `CC_CELL`.
6. **Internacional sub-tab** → `_select_cc_radio(SUBTAB_INTERNAC)` →
   wait for `CC_TABLE_INT` to become visible → `_expand_all_ver_mas(anchor_selector=CC_TABLE_INT)`
   → parse with `CC_TABLE_INT` / `CC_ROW` / `CC_CELL`. Tolerates the table
   never rendering (user has no USD movements).

## Checking — "Saldos y últimos movimientos"

```
TABLE   = 'table.Table__dataTable'
HEADERS = 'th.TableHead__headColumn'
ROW     = 'tbody tr'
CELL    = 'td.TableBody__cell'
```

7 columns; first (`id`) is hidden (`display:none`), last is a trailing action/icon cell — skip both.

| idx | meaning     | header     | example          |
|-----|-------------|------------|------------------|
| 0   | id          | id (hidden)| `1351170312`     |
| 1   | fecha       | Fecha      | `18-05-2026`     |
| 2   | descripción | Descripción| `TEF X-X NOMBRE` |
| 3   | ciudad      | Ciudad     | `SANTIAGO`       |
| 4   | monto       | Monto      | varies           |
| 5   | saldo       | Saldo      | varies           |
| 6   | (action)    | (icon)     | empty            |

Date format: `dd-mm-yyyy`.

## Credit card (billed + unbilled, same MFE)

```
TAB_BILLED      = 'button#tab-action__movimientos-facturados'
TAB_UNBILLED    = 'button#tab-action__movimientos-no-facturados'
TAB_BALANCE     = 'button#tab-action__saldo'
ACTIVE_TAB      = 'button[id^="tab-action__"].tab__action--active'

# Sub-tabs are <button class="button button--tab tab__action"> — NOT radios.
SUBTAB_NACIONAL = 'button.tab__action:has-text("Nacional"):not(:has-text("Internacional"))'
SUBTAB_INTERNAC = 'button.tab__action:has-text("Internacional")'

TABLE_NAC = 'div.tabla__movimientos--nacional table.table'
TABLE_INT = 'div.tabla__movimientos--internacional table.table'
HEAD      = 'thead.table__header tr.table__row th.table__header-item'
ROW       = 'tbody tr.table__row'
CELL      = 'td.table__data span'
```

⚠️ `.tab__action--active` is NOT unique (matches 2–3 nodes). Always combine with `id^="tab-action__"` to identify the relevant tab.

⚠️ The Nacional/Internacional toggle is a sub-tab **button** (`button.tab__action`),
NOT a radio input. The legacy `RADIO_NACIONAL` / `RADIO_INTERNAC` names in the
scraper are kept as aliases of `SUBTAB_NACIONAL` / `SUBTAB_INTERNAC` for
backwards compatibility with older tests; new code should use the `SUBTAB_*`
names. The Nacional label appears in both buttons ("Nacional" and
"Internacional"), so `SUBTAB_NACIONAL` uses
`:has-text("Nacional"):not(:has-text("Internacional"))` to disambiguate.

The portal renders only ~5 rows per sub-tab by default and appends a
**"Ver más Movimientos facturados"** / **"Ver más Movimientos por facturar"**
button below the table. This button MUST be clicked (and repeatedly clicked
until exhausted) to load the full period — without it, the scraper silently
drops everything past row 5.

### Pagination

`_expand_all_ver_mas` clicks any `button:has-text('Ver más Movimientos')` or
`button:has-text('Ver más')` inside the active pane until none remain
actionable (capped at 30 iterations to avoid loops). Each iteration scrolls
the `anchor_selector` (`CC_TABLE_NAC` or `CC_TABLE_INT`) into view first so
the section-specific button is mounted in the viewport before the click pass.
Called once per sub-tab, after `_select_cc_radio` succeeds and before
extraction.

### Nacional columns

| idx | meaning     | example          |
|-----|-------------|------------------|
| 0   | fecha       | `04/04/2026`     |
| 1   | descripción | `PAGO EN EFECTIVO`, `COLMENA`, `PATAGUA TIENDA` |
| 2   | ciudad      | `SANTIAGO` or blank `<span class="no-wrap"> </span>` |
| 3   | monto       | `$-1.089.139` (abono) or `$205.813` (cargo) |
| 4   | (icon)      | empty            |

Date format: `dd/mm/yyyy`. **Sign convention:** negative = abono/payment (inverted vs accounting standard). Parser must invert if storing as `negative = spending`.

### Internacional columns

Headers: `FECHA, DESCRIPCIÓN, PAÍS, REFERENCIA, MONTO`.

| idx | meaning     | notes                                              |
|-----|-------------|----------------------------------------------------|
| 0   | fecha       | `dd/mm/yyyy`                                       |
| 1   | descripción | merchant (e.g. `NOTION LABS, INC.`)                |
| 2   | país        | 2-letter code; may be blank                        |
| 3   | referencia  | numeric (`-23,80`, `58,84`) — original ccy         |
| 4   | monto       | currency-prefixed (`USD -23,80`) — NOT CLP        |

⚠️ tbody includes summary rows (`TOTAL PAGOS`, `TOTAL COMPRAS`) with empty fecha cell — **filter rows where idx 0 is blank**.

## Limitations

The scraper was developed and live-tested against a single-product profile: one cuenta corriente and one Visa Enjoy credit card. As a result:

- `CHECKING_SHELL_URL` hardcodes `?type=CTACTE`. Profiles with additional cuentas (other `CTACTE`, or `CTAH` / `CTANI` / `CTAV` variants) would be missed. To support them, read the account list on the checking shell and iterate `?type=CTACTE|CTAH|CTANI|CTAV` (and any future codes).
- `CC_SHELL_URL` omits `card=NNNN`; the portal auto-picks the only card when there is one. Profiles with multiple cards would only return the default one. To support them, read the card dropdown inside the CC iframe and iterate `?card=NNNN`.

No multi-account/multi-card selection logic is implemented or tested. PRs welcome — start in `ScotiabankScraper.scrape` and the URL constants near the top of the class.

## Sanitization rules (applied to committed fixtures)

- RUTs → `XX.XXX.XXX-X` / `XXXXXXX-X`
- Card last-4 (`****1234`) → `****XXXX`
- Names after transfer prefixes (TEF / TRANSF / ABONO / SBP / PAGO TARJETA) → `NOMBRE APELLIDO`
- Greeting `Hola, <name>` → `Hola, USUARIO`
- Long account numbers in `<td>` (9+ digits) → `XXXXXXXXX`
- Tax payment IDs (`SII XXXXXXXX`) → masked
- Amounts kept (helps test parser numerics)
- Dates kept (current month — non-identifying)
