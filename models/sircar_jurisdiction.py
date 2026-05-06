"""Jurisdicción adherida al SIRCAR (RG CA 84/2002 y modificatorias).

Modelo propio del módulo. No extiende nativos. La FK a `res.country.state`
permite enlazar con el campo nativo de provincia argentina sin tocarlo.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SircarJurisdiction(models.Model):
    _name = "yaguven.sircar.jurisdiction"
    _description = "Jurisdicción SIRCAR (CM)"
    _order = "cm_code"
    _rec_name = "name"

    name = fields.Char(string="Nombre", required=True, translate=False)
    cm_code = fields.Char(
        string="Código CM",
        size=3,
        required=True,
        help="Código de Convenio Multilateral de la jurisdicción "
             "(p. ej. 913 = Mendoza, 921 = Santa Fe, 914 = Misiones). "
             "Va al campo 11 del Anexo I/II.",
    )
    state_id = fields.Many2one(
        "res.country.state",
        string="Provincia (Odoo)",
        domain="[('country_id.code', '=', 'AR')]",
        help="FK a la provincia nativa argentina, opcional.",
    )
    frequency = fields.Selection(
        [("monthly", "Mensual"), ("biweekly", "Quincenal")],
        string="Frecuencia DDJJ",
        default="monthly",
        required=True,
    )
    is_perception_agent = fields.Boolean(
        string="Agente de Percepción",
        help="El cliente actúa como agente de percepción en esta jurisdicción.",
    )
    is_retention_agent = fields.Boolean(
        string="Agente de Retención",
        help="El cliente actúa como agente de retención en esta jurisdicción.",
    )
    notes = fields.Text(string="Notas")
    active = fields.Boolean(default=True)
    regime_ids = fields.One2many(
        "yaguven.sircar.regime", "jurisdiction_id", string="Tipos de régimen",
    )
    regime_count = fields.Integer(
        compute="_compute_regime_count",
    )

    _sql_constraints = [
        ("uniq_cm_code", "unique(cm_code)",
         "El código CM debe ser único."),
    ]

    @api.constrains("cm_code")
    def _check_cm_code(self):
        for r in self:
            if not r.cm_code or not r.cm_code.isdigit() or len(r.cm_code) != 3:
                raise ValidationError(
                    _("El código CM debe ser numérico de 3 dígitos. "
                      "Recibido: %s") % r.cm_code
                )

    @api.depends("regime_ids")
    def _compute_regime_count(self):
        for r in self:
            r.regime_count = len(r.regime_ids)

    def action_view_regimes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Regímenes - %s") % self.name,
            "res_model": "yaguven.sircar.regime",
            "view_mode": "list,form",
            "domain": [("jurisdiction_id", "=", self.id)],
            "context": {"default_jurisdiction_id": self.id},
        }
