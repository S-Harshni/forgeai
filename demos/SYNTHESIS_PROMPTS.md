# How the demo documents were made

All demo documents are fictional and were **synthesized by an LLM (Claude)**. The company
"Meridian Shipping Ltd" and the suppliers are invented. The source texts are in `demos/_src/`.
PDFs are rendered from them with `demos/make_pdfs.sh` (macOS `cupsfilter`), so the factory has
to deal with real PDF extraction quirks such as broken words and hard line wraps.

## Demo 1 - HR policy assistant (3 PDFs)

> Write three short HR policy documents (300-400 words each) for a fictional shipping company,
> "Meridian Shipping Ltd", covering its shore (office) staff: (1) a leave policy covering annual,
> sick, parental and compassionate leave, and public holidays; (2) a travel and expenses policy covering
> booking classes, hotel caps, meal allowances, claim deadlines and mileage; (3) a hybrid work and
> code of conduct policy covering remote days, core hours, gifts, speak-up and disciplinary appeals.
> Use numbered sections and include concrete numbers (days, USD amounts, deadlines) so that
> answers can be checked exactly. Do NOT include payroll, tax or pension information.

The brief deliberately also asks for **payslip tax and pension deductions**, which the documents do
not contain. The groundability guard should flag that.

## Demo 2 - Ship-spares procurement assistant (CSV + 2 PDFs + Markdown)

> Create a spare parts catalogue CSV for a fictional ship operator, with 24 rows and these columns:
> part_no, description, engine_model, category, supplier, unit_price_usd, lead_time_days,
> stock_qty, min_order_qty. Cover MAN B&W and Wartsila engines, purifiers, compressors, pumps,
> a boiler and steering gear, with two suppliers. Then write each supplier's terms of supply
> (pricing and discounts, payment, delivery and lead-time definition, warranty and returns,
> certification), deliberately different between the two suppliers. Then write a one-page technical
> procurement policy with approval thresholds by order value, supplier selection rules, critical spares
> and urgent orders.

The brief deliberately also asks for **supplier on-time delivery ratings**, which are not in any
document, and it needs tools (CSV lookup, calculator, current date) but **not** weather.
