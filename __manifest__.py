{
    "name": "Yagüven SIRCAR — DDJJ Agentes Recaudación IIBB",
    "version": "19.0.1.0.6",
    "summary": (
        "Genera el TXT de DDJJ del Sistema de Recaudación y Control de "
        "Agentes de Recaudación (COMARB) — Anexo I percepciones, Anexo II "
        "retenciones — para todas las jurisdicciones adheridas."
    ),
    "category": "Accounting/Localizations/Argentina",
    "author": "Yagüven C.G.",
    "website": "https://yaguven.com",
    "license": "LGPL-3",
    "depends": [
        "account",
        "l10n_ar",
        "l10n_ar_withholding",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/sircar_jurisdiction_data.xml",
        "data/sircar_regimes_mendoza.xml",
        "views/sircar_jurisdiction_views.xml",
        "views/sircar_regime_views.xml",
        "views/sircar_tax_mapping_views.xml",
        "views/sircar_partner_condition_views.xml",
        "views/sircar_export_wizard_views.xml",
        "views/sircar_menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
