# Scotiabank Chile — fixtures

Hand-crafted HTML mirroring the DOM that `ScotiabankScraper` reads. Used by
the fixture-driven tests in `tests/scrapers/cl/test_scotiabank.py`. All
identifying values follow the placeholder conventions in `CONTRIBUTING.md`.

| File | Origin URL fragment | Last updated | What it represents |
|------|---------------------|--------------|--------------------|
| `login_page.html` | `banco.scotiabank.cl/mfe-login/scotia` | 2026-05-16 | Anonymous public SPA login form. No auth required to view. |
| `checking_movements.html` | `mfe/ltmnsw/mfe-accounts-balancesmovements-web/?tab=saldos&type=CTACTE` | 2026-05-16 | Iframe content for "Saldos y últimos movimientos" of a cuenta corriente. Table class `Table__dataTable`; cells `TableBody__cell`. Date format `dd-mm-yyyy`. |
| `credit_card_billed.html` | `mfe-simple-account-statement-web-cl/?tab=movimientos-facturados` | 2026-05-16 | Iframe content with **Movimientos facturados** tab active. Contains nacional + internacional tables (`tabla__movimientos--nacional`, `tabla__movimientos--internacional`), already fully expanded. |
| `credit_card_unbilled.html` | `mfe-simple-account-statement-web-cl/?tab=movimientos-no-facturados` | 2026-05-16 | Same iframe but with **Movimientos por facturar** tab active. Same table classes; different `tab__action--active` placement. Also fully expanded. |

## What the live run reads beyond these fixtures

The captured DOM is a post-expansion, post-sub-tab-switch snapshot. Several
moving parts of `ScotiabankScraper` therefore have **no fixture coverage** and
are only exercised in live runs:

- **`_expand_all_ver_mas` pagination.** Both CC fixtures already contain the
  fully expanded tables (no `Ver más Movimientos facturados` / `Ver más
  Movimientos por facturar` button remains in the saved HTML). The click loop,
  the 30-iteration cap, and the `anchor_selector` scroll-into-view behavior
  are not triggered by the fixture tests.
- **Nacional/Internacional sub-tab switch.** `SUBTAB_NACIONAL` and
  `SUBTAB_INTERNAC` are clicked at runtime to unhide each pane, but the
  saved HTML already has both `div.tabla__movimientos--nacional` and
  `div.tabla__movimientos--internacional` tables present and parseable.
  `_select_cc_radio` itself is not exercised here.
- **`_get_stage_frame` inner-frame filtering.** The fixtures are dumps of the
  inner content frame only; the outer `mfe-shell` wrapper that also matches
  the URL fragment is not present in the saved HTML. The filter that excludes
  frames whose URL contains `mfe-shell` is therefore only validated live.

Regressions in any of the above will pass the fixture suite and only surface
in `scripts/run_all_scrapers_visible.py` against a real session.

## Sign conventions

- **Checking** (`checking_movements.html`): amounts in a dedicated column; sign follows the standard convention.
- **CC nacional**: amount column displays a leading minus for an abono/payment and no sign for a cargo/spending (inverted vs the typical accounting convention). Parser must invert if storing as `negative = spending`.

## Important parsing notes

- Both CC tables include summary rows (`TOTAL PAGOS`, `TOTAL COMPRAS`) with an empty fecha cell — filter out rows where the first cell is blank.
- Checking table has 7 columns; index 0 is hidden (`display: none`) and index 6 is a trailing action/icon cell — skip both when parsing.
- For CC internacional, the "MONTO" column shows currency-prefixed values (e.g. `USD -23,80`) — preserve currency separately from the number.

See `recon/scotiabank_notes.md` (gitignored) for the full selector documentation and the manual capture flow.

## Source profile caveat

These fixtures describe a single-product profile: one cuenta corriente (CTACTE) and one credit card. Because the portal auto-selects the only available product, the captured DOM does not include the multi-account or multi-card selectors (no `?type=` switcher UI, no `card=NNNN` dropdown). Profiles with multiple products would need additional fixtures to cover the selection step.
