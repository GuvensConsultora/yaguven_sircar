"""Wizard generador del TXT SIRCAR (Anexo I percepciones / Anexo II retenciones).

Datasources de RETENCIONES (aditivos, sin duplicación):
1) Custom: `account.payment.group.withholding` — filas del módulo
   `yaguven_payment_group` (soft-detect, si el módulo no está instalado
   este datasource queda vacío).
2) Nativo: `account.move.line` con `tax_line_id` — cubre dos casos:
   a) Asientos contables manuales o cargados por migración (move tipo
      `entry` sin payment asociado).
   b) Retenciones del motor nativo `l10n_ar_withholding` (`account.payment`
      sin `payment_group_id`).

Si `yaguven_payment_group` está instalado, el datasource (2) excluye los
moves que vienen de un `payment_group` para no duplicar lo que ya está
en (1). Los tres orígenes conviven sin solapamiento.

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
        """Líneas con `tax_line_id` apuntando al tax.

        Cubre dos orígenes:
        - Asientos de migración / manuales (move tipo `entry` sin payment).
        - Retenciones nativas `l10n_ar_withholding` (vía
          `l10n_ar.payment.register.withholding`).

        Si `yaguven_payment_group` está instalado, se excluyen las líneas
        cuyo move venga de un `account.payment.group` (esas ya las cubre
        el datasource custom y duplicarían).

        Nota: Odoo hace INNER JOIN al navegar FKs en dominios. Por eso
        el filtro de exclusión se arma como OR explícito: o bien el move
        no tiene payment asociado, o bien el payment no pertenece a un
        group. Sin el OR las líneas de asientos `entry` sin payment
        quedarían fuera del JOIN.
        """
        domain = [
            ("company_id", "=", self.company_id.id),
            ("tax_line_id", "in", taxes.ids),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
            ("parent_state", "=", "posted"),
        ]
        Payment = self.env.get("account.payment")
        if Payment is not None and "payment_group_id" in Payment._fields:
            domain += [
                "|",
                ("move_id.origin_payment_id", "=", False),
                ("move_id.origin_payment_id.payment_group_id", "=", False),
            ]
        return self.env["account.move.line"].search(domain)

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

        # Datasources aditivos pero SIN duplicación:
        # 1) Custom yaguven_payment_group (si está instalado).
        # 2) Nativo (account.move.line con tax_line_id) — incluye asientos
        #    manuales / migrados Y retenciones de l10n_ar_withholding.
        #    Si el custom está instalado, _retentions_from_native ya filtra
        #    los moves que vienen de un payment_group para evitar duplicar
        #    los del datasource (1).
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

        for ln in self._retentions_from_native(taxes):
            move = ln.move_id
            partner = move.partner_id or ln.partner_id
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
            # Base imponible: tres estrategias en cascada.
            # 1) Líneas del mismo move con `tax_ids` que contiene el tax
            #    (caso típico: payment register withholding nativo, donde
            #    una línea es la base imponible y otra la retención).
            # 2) Campo `tax_base_amount` de la propia línea de tax (caso
            #    típico: asientos `entry` de migración manual cargados con
            #    la base seteada explícitamente).
            # 3) Derivar la base como `monto_retenido / alícuota_del_tax`
            #    (último recurso, requiere tax con amount fijo > 0).
            base_lines = move.line_ids.filtered(
                lambda l: l.tax_line_id != ln.tax_line_id
                and ln.tax_line_id in l.tax_ids
            )
            base = abs(sum(base_lines.mapped("balance")))
            amt = abs(ln.balance)
            if not base and ln.tax_base_amount:
                base = abs(ln.tax_base_amount)
            if not base and ln.tax_line_id.amount:
                base = round(amt / (ln.tax_line_id.amount / 100.0), 2)
            rate = (amt / base * 100) if base else 0.0
            voucher = (move.l10n_latam_document_number
                       or move.ref
                       or move.name
                       or "")
            renglon += 1
            rows.append([
                str(renglon).zfill(5),
                "1",
                "1",
                self._split_voucher(voucher),
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
