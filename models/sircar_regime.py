"""Tipo de régimen de retención/percepción por jurisdicción.

Tabla maestra que cada jurisdicción adherida publica con sus códigos
propios (campo 10 del Anexo I/II del TXT, RG CA 2/2011).
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SircarRegime(models.Model):
    _name = "yaguven.sircar.regime"
    _description = "Tipo de régimen SIRCAR (perc/ret) por jurisdicción"
    _order = "jurisdiction_id, kind, code"

    jurisdiction_id = fields.Many2one(
        "yaguven.sircar.jurisdiction",
        string="Jurisdicción",
        required=True,
        ondelete="cascade",
    )
    code = fields.Char(
        string="Código régimen",
        size=3,
        required=True,
        help="Código numérico de 3 dígitos publicado por la jurisdicción "
             "(p. ej. 001, 011, 042...). Si la jurisdicción no definió "
             "ninguno, usar 001.",
    )
    name = fields.Char(string="Descripción", required=True)
    kind = fields.Selection(
        [("perception", "Percepción"), ("retention", "Retención")],
        string="Tipo",
        required=True,
    )
    notes = fields.Text(string="Notas")
    active = fields.Boolean(default=True)
    display_name = fields.Char(compute="_compute_display_name", store=False)

    _sql_constraints = [
        ("uniq_jurisdiction_code_kind",
         "unique(jurisdiction_id, code, kind)",
         "El (jurisdicción, código, tipo) debe ser único."),
    ]

    @api.constrains("code")
    def _check_code(self):
        for r in self:
            if not r.code or not r.code.isdigit() or len(r.code) != 3:
                raise ValidationError(
                    _("El código de régimen debe ser numérico de 3 dígitos. "
                      "Recibido: %s") % r.code
                )

    @api.depends("jurisdiction_id.name", "code", "name", "kind")
    def _compute_display_name(self):
        for r in self:
            r.display_name = "%s · %s · %s — %s" % (
                r.jurisdiction_id.name or "-",
                dict(r._fields["kind"].selection).get(r.kind, r.kind or ""),
                r.code or "",
                r.name or "",
            )
