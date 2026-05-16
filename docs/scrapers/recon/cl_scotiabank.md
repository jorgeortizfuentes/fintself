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

RADIO_NACIONAL  = 'label.label--radio:has-text("Nacionales")'
RADIO_INTERNAC  = 'label.label--radio:has-text("Internacionales")'

TABLE_NAC = 'div.tabla__movimientos--nacional table.table'
TABLE_INT = 'div.tabla__movimientos--internacional table.table'
HEAD      = 'thead.table__header tr.table__row th.table__header-item'
ROW       = 'tbody tr.table__row'
CELL      = 'td.table__data span'
```

⚠️ `.tab__action--active` is NOT unique (matches 2–3 nodes). Always combine with `id^="tab-action__"` to identify the relevant tab.

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

## Sanitization rules (applied to committed fixtures)

- RUTs → `XX.XXX.XXX-X` / `XXXXXXX-X`
- Card last-4 (`****1234`) → `****XXXX`
- Names after transfer prefixes (TEF / TRANSF / ABONO / SBP / PAGO TARJETA) → `NOMBRE APELLIDO`
- Greeting `Hola, <name>` → `Hola, USUARIO`
- Long account numbers in `<td>` (9+ digits) → `XXXXXXXXX`
- Tax payment IDs (`SII XXXXXXXX`) → masked
- Amounts kept (helps test parser numerics)
- Dates kept (current month — non-identifying)
