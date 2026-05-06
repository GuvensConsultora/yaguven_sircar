"""Wizard generador del TXT SIRCAR (Anexo I percepciones / Anexo II retenciones).

Datasources de RETENCIONES (excluyentes, no aditivos):
- Si `yaguven_payment_group` está instalado → leer SOLO de
  `account.payment.group.withholding`. Las líneas de `account.move.line`
  con `tax_line_id` del asiento generado por el group quedan cubiertas
  por ese datasource y no se vuelven a leer (evita duplicación).
- Si NO está instalado → leer del motor nativo `l10n_ar_withholding`
  (`account.move.line` con `tax_line_id`).

Datasources de PERCEPCIONES: siempre desde `account.move` (facturas de
venta posteadas con el tax aplicado en `invoice_line_ids.tax_ids`).

Filtros comunes:
- mapping `yaguven.sircar.tax.mapping` (tax_id ∈ regímenes de la jurisdicción)
- período (date_from / date_to)
- compañía
- kind (perception/retention) — define qué Anexo se genera

Genera TXT delimitado por comas según RG CA 2/2011, sin encabezado.
"""
import base64
from io import StringIO

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SircarExportWizard(models.TransientModel):
    _name = "yaguven.sircar.export.wizard"
    _description = "Generar TXT SIRCAR"

    company_id = fields.Many2one(
        "res.company", required=True,
        default=lambda self: self.env.company,
    )
    jurisdiction_id = fields.Many2one(
        "yaguven.sircar.jurisdiction",
        string="Jurisdicción",
        required=True,
    )
    kind = fields.Selection(
        [("perception", "Percepciones (Anexo I)"),
         ("retention", "Retenciones (Anexo II)")],
        string="Tipo de DDJJ",
        required=True,
        default="retention",
    )
    date_from = fields.Date(string="Desde", required=True)
    date_to = fields.Date(string="Hasta", required=True)
    file_txt = fields.Binary(string="TXT SIRCAR", readonly=True)
    file_name = fields.Char(default="sircar.txt")
    log = fields.Text(readonly=True)

    # === Helpers ===

    def _clean_cuit(self, vat):
        if not vat:
            return ""
        return "".join(c for c in str(vat) if c.isdigit())

    def _format_date(self, d):
        return d.strftime("%d/%m/%Y") if d else ""

    def _format_amount(self, amount):
        # 999999999.99 — punto decimal, sin separador de miles, 2 decimales
        return f"{round(amount or 0.0, 2):.2f}"

    def _format_rate(self, rate):
        # 999.99
        return f"{round(rate or 0.0, 2):.2f}"

    def _comprobante_perc_code(self, move):
        """Mapea move_type → código tabla 4.3 SIRCAR."""
        mt = move.move_type
        if mt == "out_invoice":
            return "1"      # Factura
        if mt == "out_refund":
            return "102"    # NC
        if mt == "in_invoice":
            return "1"
        if mt == "in_refund":
            return "102"
        return "20"         # Otros débito

    def _split_voucher(self, doc_number):
        """'00007-00014366' → '0000700014366' (13 chars). Anexo pide 12,
        pero la realidad AR es 13 (5 PV + 8 número). El estándar SIRCAR
        permite hasta 12 num; si llega 13 se trunca PV a 4."""
        if not doc_number:
            return "000000000000"
        clean = "".join(c for c in doc_number if c.isdigit())
        return clean[-12:].zfill(12)

    # === Datasource: retenciones del custom payment_group ===

    def _retentions_from_payment_group(self, taxes):
        """Lee account.payment.group.withholding si el modelo existe."""
        Model = self.env.get("account.payment.group.withholding")
        if Model is None:
            return self.env["account.payment.group.withholding"]  # vacío
        return Model.search([
            ("company_id", "=", self.company_id.id),
            ("tax_id", "in", taxes.ids),
            ("payment_group_id.payment_date", ">=", self.date_from),
            ("payment_group_id.payment_date", "<=", self.date_to),
            ("payment_group_id.state", "=", "posted"),
        ])

    # === Datasource: retenciones nativas en l10n_ar_withholding_ids ===

    def _retentions_from_native(self, taxes):
        """Account.move tipo `entry` que sean asientos de retención
        nativa (l10n_ar_withholding) y referencien estos taxes en sus
        líneas."""
        # En 19 las retenciones nativas se aplican vía
        # l10n_ar.payment.register.withholding y crean account.move.line
        # con tax_line_id apuntando al tax. Filtramos por línea.
        lines = self.env["account.move.line"].search([
            ("company_id", "=", self.company_id.id),
            ("tax_line_id", "in", taxes.ids),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
            ("parent_state", "=", "posted"),
        ])
        return lines

    # === Generación del TXT ===

    def _build_lines_retention(self):
        mapping = self.env["yaguven.sircar.tax.mapping"].search([
            ("company_id", "=", self.company_id.id),
            ("regime_id.jurisdiction_id", "=", self.jurisdiction_id.id),
            ("kind", "=", "retention"),
            ("active", "=", True),
        ])
        if not mapping:
            raise UserError(
                _("No hay mapping de impuestos de retención cargado para "
                  "%s. Ir a SIRCAR > Mapping y agregar al menos uno.")
                % self.jurisdiction_id.name
            )
        tax_to_regime = {m.tax_id.id: m.regime_id for m in mapping}
        taxes = mapping.mapped("tax_id")
        cm_code = self.jurisdiction_id.cm_code

        rows = []
        renglon = 0
        log_lines = []

        # Datasource ÚNICO (excluyente, no aditivo): si yaguven_payment_group
        # está instalado, todas las retenciones pasan por su tabla propia,
        # y el motor nativo l10n_ar_withholding no se consulta para evitar
        # duplicar la misma retención que también aparece como account.move.line
        # del asiento generado por el group.
        if self.env.get("account.payment.group.withholding") is not None:
            for w in self._retentions_from_payment_group(taxes):
                partner = w.payment_group_id.partner_id
                cuit = self._clean_cuit(partner.vat)
                if not cuit or len(cuit) != 11:
                    log_lines.append(
                        f"  WARN payment_group_id={w.payment_group_id.id} "
                        f"partner='{partner.name}' CUIT inválido='{cuit}'"
                    )
                    continue
                regime = tax_to_regime.get(w.tax_id.id)
                if not regime:
                    continue
                base = w.base_amount or 0.0
                amt = w.amount or 0.0
                rate = (amt / base * 100) if base else 0.0
                renglon += 1
                rows.append([
                    str(renglon).zfill(5),
                    "1",                               # origen software propio
                    "1",                               # tipo: comprobante
                    self._split_voucher(w.name or ""),
                    cuit,
                    self._format_date(w.payment_group_id.payment_date),
                    self._format_amount(base),
                    self._format_rate(rate),
                    self._format_amount(amt),
                    regime.code,
                    cm_code,
                ])
        else:
            for ln in self._retentions_from_native(taxes):
                move = ln.move_id
                partner = move.partner_id
                cuit = self._clean_cuit(partner.vat)
                if not cuit or len(cuit) != 11:
                    log_lines.append(
                        f"  WARN move_id={move.id} name='{move.name}' "
                        f"partner='{partner.name}' CUIT inválido='{cuit}'"
                    )
                    continue
                regime = tax_to_regime.get(ln.tax_line_id.id)
                if not regime:
                    continue
                # Base: líneas del move con el tax en `tax_ids` y que NO sean
                # la propia línea de tax.
                base_lines = move.line_ids.filtered(
                    lambda l: l.tax_line_id != ln.tax_line_id
                    and ln.tax_line_id in l.tax_ids
                )
                base = abs(sum(base_lines.mapped("balance")))
                amt = abs(ln.balance)
                rate = (amt / base * 100) if base else 0.0
                renglon += 1
                rows.append([
                    str(renglon).zfill(5),
                    "1",
                    "1",
                    self._split_voucher(move.l10n_latam_document_number or ""),
                    cuit,
                    self._format_date(move.date),
                    self._format_amount(base),
                    self._format_rate(rate),
                    self._format_amount(amt),
                    regime.code,
                    cm_code,
                ])

        return rows, log_lines

    def _build_lines_perception(self):
        mapping = self.env["yaguven.sircar.tax.mapping"].search([
            ("company_id", "=", self.company_id.id),
            ("regime_id.jurisdiction_id", "=", self.jurisdiction_id.id),
            ("kind", "=", "perception"),
            ("active", "=", True),
        ])
        if not mapping:
            raise UserError(
                _("No hay mapping de impuestos de percepción cargado para "
                  "%s.") % self.jurisdiction_id.name
            )
        tax_to_regime = {m.tax_id.id: m.regime_id for m in mapping}
        taxes = mapping.mapped("tax_id")
        cm_code = self.jurisdiction_id.cm_code

        rows = []
        renglon = 0
        log_lines = []

        # Líneas de tax aplicadas en facturas de venta (out_invoice/refund)
        moves = self.env["account.move"].search([
            ("company_id", "=", self.company_id.id),
            ("move_type", "in", ("out_invoice", "out_refund")),
            ("state", "=", "posted"),
            ("invoice_date", ">=", self.date_from),
            ("invoice_date", "<=", self.date_to),
            ("invoice_line_ids.tax_ids", "in", taxes.ids),
        ])
        for move in moves:
            partner = move.partner_id
            cuit = self._clean_cuit(partner.vat)
            if not cuit or len(cuit) != 11:
                log_lines.append(
                    f"  WARN move_id={move.id} name='{move.name}' "
                    f"partner='{partner.name}' CUIT inválido='{cuit}'"
                )
                continue
            # Por cada tax mapeado presente en el move
            for tax in taxes:
                base_lines = move.invoice_line_ids.filtered(
                    lambda l: tax in l.tax_ids
                )
                if not base_lines:
                    continue
                regime = tax_to_regime[tax.id]
                base = sum(base_lines.mapped("price_subtotal"))
                tax_lines = move.line_ids.filtered(
                    lambda l: l.tax_line_id == tax
                )
                amt = abs(sum(tax_lines.mapped("balance")))
                rate = (amt / base * 100) if base else 0.0
                letter = (move.l10n_latam_document_type_id.l10n_ar_letter
                          or "Z")
                renglon += 1
                rows.append([
                    str(renglon).zfill(5),
                    self._comprobante_perc_code(move),
                    letter,
                    self._split_voucher(move.l10n_latam_document_number or ""),
                    cuit,
                    self._format_date(move.invoice_date),
                    self._format_amount(base),
                    self._format_rate(rate),
                    self._format_amount(amt),
                    regime.code,
                    cm_code,
                ])
        return rows, log_lines

    def action_generate(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("La fecha 'desde' es posterior a 'hasta'."))
        if self.kind == "retention":
            if not self.jurisdiction_id.is_retention_agent:
                raise UserError(
                    _("La jurisdicción %s no está marcada como agente de "
                      "retención. Revisá la configuración.")
                    % self.jurisdiction_id.name
                )
            rows, log_lines = self._build_lines_retention()
        else:
            if not self.jurisdiction_id.is_perception_agent:
                raise UserError(
                    _("La jurisdicción %s no está marcada como agente de "
                      "percepción.") % self.jurisdiction_id.name
                )
            rows, log_lines = self._build_lines_perception()

        if not rows:
            raise UserError(
                _("No se encontraron movimientos para la jurisdicción "
                  "%s en el período %s — %s.")
                % (self.jurisdiction_id.name, self.date_from, self.date_to)
            )

        # Volcar a TXT
        buf = StringIO()
        for row in rows:
            buf.write(",".join(row) + "\n")
        content = buf.getvalue().encode("utf-8")
        self.file_txt = base64.b64encode(content)
        period = self.date_from.strftime("%Y%m")
        anexo = "I" if self.kind == "perception" else "II"
        self.file_name = (
            f"SIRCAR_{self.jurisdiction_id.cm_code}_"
            f"{period}_anexo{anexo}.txt"
        )
        self.log = (
            f"Renglones: {len(rows)}\n"
            f"Período: {self.date_from} → {self.date_to}\n"
            f"Jurisdicción: {self.jurisdiction_id.name} "
            f"(CM {self.jurisdiction_id.cm_code})\n"
        )
        if log_lines:
            self.log += "\nWarnings:\n" + "\n".join(log_lines)

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
