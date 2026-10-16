"""A transparent UK take-home estimate, inside vs outside IR35 (PLAN.md Step 21c).

An approximation to make two offers comparable, NOT tax advice. Every rate comes
from `config/uk_tax.yml` (marked unverified); pension, student loan, VAT and
expenses are ignored. Inside IR35 is modelled as an umbrella company (fee and
employer costs come out of the invoice, then PAYE); outside as a one-director
limited company paying a salary at the personal allowance and the rest as
dividends after corporation tax.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parents[4] / "config" / "uk_tax.yml"


@dataclass(frozen=True)
class TaxConfig:
    """The tax assumptions (see `config/uk_tax.yml`).

    Attributes:
        tax_year: The tax year the figures are for.
        verified: Whether someone has checked them against HMRC.
        raw: The parsed YAML sections.
    """

    tax_year: str
    verified: bool
    raw: dict

    def __getitem__(self, section: str) -> dict:
        """Return one config section.

        Args:
            section: The section name, e.g. `income_tax`.

        Returns:
            The section's mapping.
        """
        return self.raw[section]


@dataclass(frozen=True)
class NetResult:
    """A take-home estimate.

    Attributes:
        status: `inside` or `outside`.
        day_rate: The day rate it was computed for.
        gross_year: Annual invoice total (day rate x days).
        steps: Labelled deductions in order, `(label, amount)`.
        net_year: Take-home per year.
        net_day: Take-home per billable day.
        assumptions: Plain-language assumptions behind the figure.
        tax_year: The tax year of the config.
        verified: Whether the config has been verified.
    """

    status: str
    day_rate: float
    gross_year: float
    net_year: float
    net_day: float
    steps: list[tuple[str, float]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    tax_year: str = ""
    verified: bool = False


def load_tax_config(path: Path | None = None) -> TaxConfig:
    """Read the tax config.

    Args:
        path: Override for tests; defaults to `config/uk_tax.yml`.

    Returns:
        The config.
    """
    raw = yaml.safe_load((path or _DEFAULT_PATH).read_text())
    return TaxConfig(str(raw["tax_year"]), bool(raw.get("verified", False)), raw)


def _stack(start: float, amount: float, bands: list[tuple[float, float]]) -> float:
    """Tax an amount stacked on top of `start` through rate bands.

    Args:
        start: Taxable income already used below this amount.
        amount: The amount to tax.
        bands: `(upper_limit, rate)` in ascending order; the last limit may be
            infinity.

    Returns:
        The tax due on `amount`.
    """
    tax, position, remaining = 0.0, start, amount
    for limit, rate in bands:
        if remaining <= 0:
            break
        room = max(0.0, limit - position)
        taken = min(room, remaining)
        tax += taken * rate
        position += taken
        remaining -= taken
    return tax


def income_tax(non_dividend: float, dividends: float, cfg: TaxConfig) -> float:
    """Income tax on employment/other income plus dividends.

    Args:
        non_dividend: Salary and other non-dividend income.
        dividends: Dividend income.
        cfg: The tax config.

    Returns:
        Income tax, with the personal allowance tapering above 100,000, the
        dividend allowance and the dividend rates.
    """
    t, d = cfg["income_tax"], cfg["dividends"]
    adjusted = non_dividend + dividends
    allowance = max(
        0.0, t["personal_allowance"] - max(0.0, adjusted - t["pa_taper_start"]) / 2
    )
    used = min(allowance, non_dividend)
    taxable_other = non_dividend - used
    allowance_left = allowance - used
    other_bands = [
        (t["basic_band"], t["basic_rate"]),
        (t["higher_limit"], t["higher_rate"]),
        (float("inf"), t["additional_rate"]),
    ]
    tax = _stack(0.0, taxable_other, other_bands)
    taxable_div = max(0.0, dividends - allowance_left)
    free = min(d["allowance"], taxable_div)
    div_bands = [
        (t["basic_band"], d["basic_rate"]),
        (t["higher_limit"], d["higher_rate"]),
        (float("inf"), d["additional_rate"]),
    ]
    # the dividend allowance is taxed at 0% but still fills band space
    tax += _stack(taxable_other + free, taxable_div - free, div_bands)
    return tax


def employee_ni(salary: float, cfg: TaxConfig) -> float:
    """Employee Class 1 National Insurance.

    Args:
        salary: Annual gross pay.
        cfg: The tax config.

    Returns:
        NI at the main rate between the thresholds and the upper rate above.
    """
    n = cfg["national_insurance"]
    main = max(
        0.0, min(salary, n["employee_upper_limit"]) - n["employee_primary_threshold"]
    )
    upper = max(0.0, salary - n["employee_upper_limit"])
    return main * n["employee_main_rate"] + upper * n["employee_upper_rate"]


def employer_ni(salary: float, cfg: TaxConfig) -> float:
    """Employer Class 1 National Insurance.

    Args:
        salary: Annual gross pay.
        cfg: The tax config.

    Returns:
        Employer NI above the secondary threshold.
    """
    n = cfg["national_insurance"]
    return max(0.0, salary - n["employer_secondary_threshold"]) * n["employer_rate"]


def corporation_tax(profit: float, cfg: TaxConfig) -> float:
    """UK corporation tax with marginal relief.

    Args:
        profit: Taxable profit.
        cfg: The tax config.

    Returns:
        The small-profits rate to the lower limit, the main rate above the upper
        limit, and the main rate less marginal relief between them.
    """
    c = cfg["corporation_tax"]
    if profit <= 0:
        return 0.0
    if profit <= c["small_profits_limit"]:
        return profit * c["small_profits_rate"]
    if profit >= c["main_rate_limit"]:
        return profit * c["main_rate"]
    return profit * c["main_rate"] - c["marginal_relief_fraction"] * (
        c["main_rate_limit"] - profit
    )


def inside_ir35_net(day_rate: float, cfg: TaxConfig) -> NetResult:
    """Take-home for a day rate inside IR35 (umbrella company).

    Args:
        day_rate: GBP per billable day.
        cfg: The tax config.

    Returns:
        The estimate: the invoice less the umbrella fee, employer NI and
        levy (solving for the gross pay they are charged on), then income
        tax and employee NI.
    """
    days = cfg["working"]["days_per_year"]
    u, n = cfg["inside_ir35_umbrella"], cfg["national_insurance"]
    invoice = day_rate * days
    available = invoice - u["umbrella_fee_per_year"]
    gross_pay = (available + n["employer_rate"] * n["employer_secondary_threshold"]) / (
        1 + n["employer_rate"] + u["apprenticeship_levy_rate"]
    )
    gross_pay = max(0.0, gross_pay)
    er_ni = employer_ni(gross_pay, cfg)
    levy = gross_pay * u["apprenticeship_levy_rate"]
    tax, ni = income_tax(gross_pay, 0.0, cfg), employee_ni(gross_pay, cfg)
    net = gross_pay - tax - ni
    return NetResult(
        status="inside",
        day_rate=day_rate,
        gross_year=invoice,
        net_year=net,
        net_day=net / days,
        steps=[
            ("Umbrella fee", u["umbrella_fee_per_year"]),
            ("Employer NI", er_ni),
            ("Apprenticeship levy", levy),
            ("Income tax (PAYE)", tax),
            ("Employee NI", ni),
        ],
        assumptions=[
            f"{days} billable days a year",
            "paid through an umbrella company; holiday pay is inside the gross pay",
            "no pension, student loan or expenses",
            "England, Wales and Northern Ireland income tax (Scotland differs)",
        ],
        tax_year=cfg.tax_year,
        verified=cfg.verified,
    )


def outside_ir35_net(day_rate: float, cfg: TaxConfig) -> NetResult:
    """Take-home for a day rate outside IR35 (own limited company).

    Args:
        day_rate: GBP per billable day.
        cfg: The tax config.

    Returns:
        The estimate: turnover less running costs, a director salary at the
        personal allowance and its employer NI, corporation tax, then the
        remainder paid as dividends and taxed.
    """
    days = cfg["working"]["days_per_year"]
    o = cfg["outside_ir35_company"]
    turnover = day_rate * days
    salary = o["director_salary"]
    er_ni = employer_ni(salary, cfg)
    profit = turnover - o["accountant_and_running_costs_per_year"] - salary - er_ni
    ct = corporation_tax(profit, cfg)
    dividends = max(0.0, profit - ct)
    tax = income_tax(salary, dividends, cfg)
    ni = employee_ni(salary, cfg)
    net = salary + dividends - tax - ni
    return NetResult(
        status="outside",
        day_rate=day_rate,
        gross_year=turnover,
        net_year=net,
        net_day=net / days,
        steps=[
            (
                "Running costs and accountant",
                o["accountant_and_running_costs_per_year"],
            ),
            ("Employer NI on director salary", er_ni),
            ("Corporation tax", ct),
            ("Income and dividend tax", tax),
            ("Employee NI", ni),
        ],
        assumptions=[
            f"{days} billable days a year",
            f"one-director company: salary {salary:,.0f}, the rest as dividends",
            "no VAT scheme, pension, student loan or expenses; all profit drawn",
            "England, Wales and Northern Ireland income tax (Scotland differs)",
            "no Employment Allowance (a sole director cannot claim it)",
        ],
        tax_year=cfg.tax_year,
        verified=cfg.verified,
    )


def net_for(day_rate: float, ir35_status: str, cfg: TaxConfig) -> NetResult:
    """Take-home for a day rate under a given IR35 status.

    Args:
        day_rate: GBP per billable day.
        ir35_status: `inside` or `outside`.
        cfg: The tax config.

    Returns:
        The estimate.

    Raises:
        ValueError: If the status is not `inside` or `outside` (an
            undetermined status cannot be compared).
    """
    if ir35_status == "inside":
        return inside_ir35_net(day_rate, cfg)
    if ir35_status == "outside":
        return outside_ir35_net(day_rate, cfg)
    raise ValueError("IR35 status must be 'inside' or 'outside' to compare take-home")
