# Scotiabank Chile — fixtures

Captured **2026-05-16** from `https://www.scotiabank.cl/` (personas) after live login, sanitized per `CONTRIBUTING.md` PII checklist.

| File | Origin URL fragment | What it represents |
|------|---------------------|--------------------|
| `login_page.html` | `banco.scotiabank.cl/mfe-login/scotia` | Anonymous public SPA login form. No auth required to capture. |
| `checking_movements.html` | `mfe/ltmnsw/mfe-accounts-balancesmovements-web/?tab=saldos&type=CTACTE` | Iframe content for "Saldos y últimos movimientos" of a cuenta corriente. Table class `Table__dataTable`; cells `TableBody__cell`. Date format `dd-mm-yyyy`. |
| `credit_card_billed.html` | `mfe-simple-account-statement-web-cl/?tab=movimientos-facturados` | Iframe content with **Movimientos facturados** tab active. Contains nacional + internacional tables (`tabla__movimientos--nacional`, `tabla__movimientos--internacional`). |
| `credit_card_unbilled.html` | `mfe-simple-account-statement-web-cl/?tab=movimientos-facturados` | Same iframe, but with **Movimientos por facturar** tab active. Same table classes; different `tab__action--active` placement. |

All four fixtures were captured via `scripts/_capture_scotiabank_auth.py` (gitignored helper) and sanitized via `scripts/_sanitize_scotiabank_fixtures.py` (also gitignored).

## Sign conventions observed

- **Checking** (`checking_movements.html`): amounts in dedicated column; sign follows standard convention.
- **CC nacional**: amount column shows `$-1.089.139` for abono/payment and `$205.813` for cargo/spending — **negative prefix denotes credit (abono), positive denotes charge** (inverted vs typical accounting convention). Parser must invert if storing as `negative = spending`.

## Important parsing notes

- Both CC tables include summary rows (`TOTAL PAGOS`, `TOTAL COMPRAS`) with an empty fecha cell — filter out rows where the first cell is blank.
- Checking table has 7 columns; index 0 is hidden (`display: none`) and index 6 is a trailing action/icon cell — skip both when parsing.
- For CC internacional, the "MONTO" column shows currency-prefixed values (e.g. `USD -23,80`) — preserve currency separately from the number.

See `recon/scotiabank_notes.md` (gitignored) for full selector documentation and the live capture flow.

## Source profile caveat

All captures come from a Scotiabank Chile user with exactly **one cuenta corriente (CTACTE) and one credit card**. Because the portal auto-selects the only available product, the recon DOM does **not** include the multi-account or multi-card selectors (no `?type=` switcher UI, no `card=NNNN` dropdown). Fixtures for multi-product profiles would need to be captured separately to test selection logic.
