"""Mapping entre `account.tax` del cliente y régimen SIRCAR.

Permite que cada impuesto contable de retención/percepción se asocie
al código de régimen de la jurisdicción al que corresponde, sin tocar
el modelo nativo `account.tax`.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SircarTaxMapping(models.Model):
    _name = "yaguven.sircar.tax.mapping"
    _description = "Mapping account.tax ↔ régimen SIRCAR"
    _order = "company_id, jurisdiction_id, regime_id"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        required=True,
        default=lambda self: self.env.company,
    )
    tax_id = fields.Many2one(
        "account.tax",
        string="Impuesto",
        required=True,
        check_company=True,
        help="Impuesto contable que representa la percepción/retención "
             "practicada por la compañía en esta jurisdicción.",
    )
    regime_id = fields.Many2one(
        "yaguven.sircar.regime",
        string="Régimen SIRCAR",
        required=True,
    )
    jurisdiction_id = fields.Many2one(
        related="regime_id.jurisdiction_id", store=True,
        string="Jurisdicción",
    )
    kind = fields.Selection(
        related="regime_id.kind", store=True, string="Tipo",
    )
    notes = fields.Char(string="Notas")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("uniq_company_tax_regime",
         "unique(company_id, tax_id, regime_id)",
         "Ya existe un mapping para esta compañía / impuesto / régimen."),
    ]

    @api.constrains("tax_id", "regime_id")
    def _check_tax_kind(self):
        """Sanity: si el régimen es retención, el tax debería tener
        l10n_ar_withholding_payment_type seteado; si es percepción,
        type_tax_use='sale'/'purchase' sin payment_type."""
        for r in self:
            tax = r.tax_id
            if r.kind == "retention":
                # Retención: en Odoo 19, los taxes tienen
                # l10n_ar_withholding_payment_type ('supplier'|'customer')
                if not tax.l10n_ar_withholding_payment_type:
                    raise ValidationError(
                        _("El impuesto '%s' está mapeado como RETENCIÓN "
                          "pero no tiene definido "
                          "l10n_ar_withholding_payment_type. "
                          "Verificar configuración del tax.") % tax.name
                    )
            elif r.kind == "perception":
                if tax.l10n_ar_withholding_payment_type:
                    raise ValidationError(
                        _("El impuesto '%s' está mapeado como PERCEPCIÓN "
                          "pero tiene definido como retención "
                          "(l10n_ar_withholding_payment_type=%s).")
                        % (tax.name, tax.l10n_ar_withholding_payment_type)
                    )
