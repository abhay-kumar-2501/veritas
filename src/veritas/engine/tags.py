"""XBRL tag fallbacks (PRD Appendix A). us-gaap unless noted; first hit per period wins."""

from __future__ import annotations

TAXONOMY = "us-gaap"

TAG_FALLBACKS: dict[str, tuple[str, ...]] = {
    "Revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ),
    "COGS": ("CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"),
    "AR": ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
    "Inventory": ("InventoryNet",),
    "AP": ("AccountsPayableCurrent", "AccountsPayableTradeCurrent"),
    "OCF": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "NetIncome": ("ProfitLoss", "NetIncomeLoss"),
    "CapEx": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    "DA": ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "Depreciation"),
    "SBC": ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
    "RnD": ("ResearchAndDevelopmentExpense",),
    "CapSW": ("PaymentsToDevelopSoftware", "CapitalizedComputerSoftwareAdditions"),
    "Goodwill": ("Goodwill",),
    "Intangibles": ("IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"),
    "Assets": ("Assets",),
    "Equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "Cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "OperatingIncome": ("OperatingIncomeLoss",),
    "PreTaxIncome": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ),
    "Tax": ("IncomeTaxExpenseBenefit",),
    "Acquisitions": ("PaymentsToAcquireBusinessesNetOfCashAcquired",),
    "Restructuring": (
        "RestructuringCharges",
        "RestructuringCostsAndAssetImpairmentCharges",
        "RestructuringAndRelatedCostIncurredCost",
    ),
    "GoodwillImpairment": ("GoodwillImpairmentLoss",),
    "AssetImpairment": ("AssetImpairmentCharges",),
    "LongLivedImpairment": ("ImpairmentOfLongLivedAssetsHeldForUse",),
    # Total debt components (computed, labelled "approximate")
    "LongTermDebt": ("LongTermDebt",),
    "LongTermDebtNoncurrent": ("LongTermDebtNoncurrent",),
    "LongTermDebtCurrent": ("LongTermDebtCurrent",),
    "ShortTermBorrowings": ("ShortTermBorrowings",),
    "CommercialPaper": ("CommercialPaper",),
}

# Metrics that are point-in-time balances (instants) rather than durations.
INSTANT_METRICS: frozenset[str] = frozenset(
    {
        "AR",
        "Inventory",
        "AP",
        "Goodwill",
        "Intangibles",
        "Assets",
        "Equity",
        "Cash",
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "LongTermDebtCurrent",
        "ShortTermBorrowings",
        "CommercialPaper",
    }
)


def all_tags() -> frozenset[str]:
    """Every us-gaap tag referenced above (used to trim seed companyfacts)."""
    return frozenset(tag for tags in TAG_FALLBACKS.values() for tag in tags)
