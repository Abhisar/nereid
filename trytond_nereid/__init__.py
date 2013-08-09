# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
from trytond.pool import Pool

from .party import Address, Party, ContactMechanism
from .routing import NereidUser, Permission, UserPermission, \
    URLMap, WebSite, URLRule, URLRuleDefaults, WebsiteCountry, \
    WebsiteCurrency, NereidStaticFolder, Language
from .static_file import NereidStaticFile
from .currency import Currency
from .template import ContextProcessors


def register():
    Pool.register(
        Address,
        Party,
        NereidUser,
        ContactMechanism,
        Permission,
        UserPermission,
        URLMap,
        WebSite,
        URLRule,
        URLRuleDefaults,
        WebsiteCountry,
        WebsiteCurrency,
        NereidStaticFolder,
        NereidStaticFile,
        Currency,
        ContextProcessors,
        Language,
        module='nereid', type_='model'
    )
