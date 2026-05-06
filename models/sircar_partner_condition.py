"""Override de régimen SIRCAR por partner y jurisdicción.

Permite indicar para cada proveedor (o cliente, en percepciones) qué
régimen específico aplicar cuando se retiene/percibe en una jurisdicción
dada — sin tocar el modelo nativo `res.partner`.

Casos típicos en Mendoza (jurisdicción 913):
  - Local inscripto → régimen 101 (default del mapping del tax)
  - Local riesgo fiscal / no inscripto → régimen 102
  - Convenio Multilateral inscripto → régimen 103
  - CM riesgo fiscal / no inscripto → régimen 104

Si un partner no tiene condición cargada para la jurisdicción, el
wizard usa el régimen default del `yaguven.sircar.tax.mapping`.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SircarPartnerCondition(models.Model):
    _name = "yaguven.sircar.partner.condition"
    _description = "Régimen SIRCAR específico por partner y jurisdicción"
    _order = "partner_id, jurisdiction_id"

    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
        required=True,
        ondelete="cascade",
        help="Proveedor (en retenciones) o cliente (en percepciones).",
    )
    jurisdiction_id = fields.Many2one(
        "yaguven.sircar.jurisdiction",
        string="Jurisdicción",
        required=True,
        ondelete="cascade",
    )
    regime_id = fields.Many2one(
        "yaguven.sircar.regime",
        string="Régimen específico",
        required=True,
        domain="[('jurisdiction_id', '=', jurisdiction_id)]",
        help="Régimen que el wizard SIRCAR usará para este partner en esta "
             "jurisdicción, sobreescribiendo el default del mapping del tax. "
             "Útil cuando el partner es Convenio Multilateral, está en "
             "padrón de riesgo fiscal, no está inscripto, o cuando la "
             "actividad cae en un inciso específico del Art. 5 RG 19/12.",
    )
    kind = fields.Selection(
        related="regime_id.kind", store=True, string="Tipo",
    )
    notes = fields.Char(string="Notas")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("uniq_partner_jurisdiction_kind",
         "unique(partner_id, jurisdiction_id, kind)",
         "Ya existe una condición de ese tipo para este partner y "
         "jurisdicción. Editá la existente en lugar de crear una nueva."),
    ]

    @api.constrains("regime_id", "jurisdiction_id")
    def _check_regime_jurisdiction(self):
        for r in self:
            if r.regime_id.jurisdiction_id != r.jurisdiction_id:
                raise ValidationError(
                    _("El régimen seleccionado pertenece a otra "
                      "jurisdicción. Régimen: %s · Jurisdicción del "
                      "registro: %s")
                    % (r.regime_id.display_name,
                       r.jurisdiction_id.name)
                )
