"""Generate two FICTIONAL sustainability reports for testing the pipeline.

They deliberately mimic real-report messiness:
  * Nordwind Logistics AG 2024 + 2025 - tCO2e and m3, EUR thousand pay; the 2025
    report RESTATES 2024 Scope 3 and LTIFR (the trend view should flag it)
  * Helios Components plc - ktCO2e and megalitres, columns in REVERSE order
    (2024 | 2025), a Scope total that does NOT add up (validation should catch
    it), distractor numbers in prose (targets, % changes), and a governance
    page that is a scanned image (needs OCR).
All companies and figures are invented.
"""
from pathlib import Path

import pypdfium2 as pdfium
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

HERE = Path(__file__).parent
ss = getSampleStyleSheet()
H1, H2, P = ss["Title"], ss["Heading2"], ss["BodyText"]


def table(rows):
    t = Table(rows, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDEBF7")),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
    ]))
    return t


NORDWIND = {   # year -> values as printed in THAT year's report
    2023: dict(s1="57,400", s2l="11,200", s2m="9,800", s3="405,300", tot="472,500", nox="251", sox="5.0",
               ww="198,000", wd="152,100", wc="45,900", emp="10,350", women="29.1%", hires="1,640",
               turn="15.3%", train="15.2", ltifr="4.4", fat="0", board="12", indep="6", wboard="4",
               corr="0", ceo="2,980"),
    # 2024 as ORIGINALLY reported. The 2025 report restates Scope 3 (new category-1
    # methodology) and LTIFR (late-reported injuries) - the trend view should catch it.
    2024: dict(s1="52,980", s2l="10,450", s2m="6,400", s3="398,700", tot="458,080", nox="236", sox="4.6",
               ww="191,200", wd="147,900", wc="43,300", emp="10,980", women="30.2%", hires="1,870",
               turn="14.1%", train="16.9", ltifr="3.6", fat="1", board="12", indep="7", wboard="4",
               corr="0", ceo="3,120"),
    2025: dict(s1="48,210", s2l="9,870", s2m="3,120", s3="412,500", tot="463,830", nox="212", sox="4.1",
               ww="185,400", wd="142,300", wc="43,100", emp="11,460", women="31.5%", hires="2,140",
               turn="12.8%", train="18.4", ltifr="3.2", fat="0", board="12", indep="8", wboard="5",
               corr="0", ceo="3,450"),
}
# comparatives printed in the 2025 report for 2024 (restated values)
NORDWIND_2024_RESTATED = {**NORDWIND[2024], "s3": "430,100", "tot": "489,480", "ltifr": "3.9"}


def nordwind(year: int = 2025):
    c = NORDWIND[year]
    p = NORDWIND_2024_RESTATED if year == 2025 else NORDWIND[year - 1]
    y, py = str(year), str(year - 1)
    netzero = ("We have committed to reach net-zero emissions across our value chain by 2045, validated by the "
               "SBTi, with an interim goal to cut Scope 1 and 2 emissions by 42% by 2030.")
    restate = (" 2024 Scope 3 emissions and LTIFR have been restated: Scope 3 now applies spend-based factors "
               "for purchased goods (category 1), and LTIFR includes late-reported injuries." if year == 2025 else "")
    s = [Paragraph("Nordwind Logistics AG", H1), Paragraph(f"Sustainability Report {year}", H2),
         Paragraph("Nordwind Logistics AG is a Hamburg-based freight and warehousing group operating "
                   f"42 sites across Europe. This report covers the financial year {year} and is prepared "
                   "with reference to the GRI Standards 2021 and ESRS.", P),
         Paragraph("Revenue reached EUR 1,840 million, up 6% year on year." if year == 2025 else
                   "Revenue reached EUR 1,736 million, up 4% year on year.", P), PageBreak(),

         Paragraph("Climate and environment", H2),
         Paragraph("Our environmental policy commits all sites to an ISO 14001 certified environmental "
                   f"management system; {38 if year == 2025 else 33} of 42 sites were certified at year end. " + netzero, P),
         Paragraph("Scope 1 emissions fell mainly due to the switch of trucks to HVO biodiesel." + restate, P),
         Spacer(1, 8),
         table([["Metric", "Unit", y, py],
                ["Scope 1 emissions", "tCO2e", c["s1"], p["s1"]],
                ["Scope 2 emissions (location-based)", "tCO2e", c["s2l"], p["s2l"]],
                ["Scope 2 emissions (market-based)", "tCO2e", c["s2m"], p["s2m"]],
                ["Scope 3 emissions (total)", "tCO2e", c["s3"], p["s3"]],
                ["Total GHG emissions (Scope 1+2+3, market-based)", "tCO2e", c["tot"], p["tot"]],
                ["NOx emissions", "t", c["nox"], p["nox"]],
                ["SOx emissions", "t", c["sox"], p["sox"]]]),
         Spacer(1, 12), Paragraph("Water", H2),
         table([["Metric", "Unit", y, py],
                ["Total water withdrawal", "m3", c["ww"], p["ww"]],
                ["Total water discharge", "m3", c["wd"], p["wd"]],
                ["Total water consumption", "m3", c["wc"], p["wc"]]]),
         PageBreak(),

         Paragraph("Our people", H2),
         Paragraph("We respect internationally recognised human rights and the ILO core labour standards. "
                   "Our Human Rights Policy and Supplier Code of Conduct prohibit forced and child labour "
                   "and protect freedom of association; a human-rights risk assessment of key suppliers is "
                   "carried out annually.", P),
         Spacer(1, 8),
         table([["Indicator", "Unit", y, py],
                ["Total employees (headcount)", "number", c["emp"], p["emp"]],
                ["Women in workforce (%)", "%", c["women"], p["women"]],
                ["New employee hires", "number", c["hires"], p["hires"]],
                ["Employee turnover rate", "%", c["turn"], p["turn"]],
                ["Average training hours per employee", "hours", c["train"], p["train"]],
                ["LTIFR (per 1 million hours worked)", "rate", c["ltifr"], p["ltifr"]],
                ["Work-related fatalities", "number", c["fat"], p["fat"]]]),
         PageBreak(),

         Paragraph("Corporate governance", H2),
         Paragraph("Nordwind has a two-tier board. The Supervisory Board is chaired by Dr. Ines Kraft, "
                   "while the Chief Executive Officer, Mark Feldmann, chairs the Management Board; the roles "
                   "of Chair and CEO are therefore held by different persons. The Supervisory Board has "
                   "three committees: the Audit Committee, the Nomination Committee and the Remuneration "
                   "Committee.", P),
         Paragraph("All employees and suppliers can report concerns anonymously through our SpeakUp "
                   "whistleblowing hotline, operated by an independent provider in 14 languages.", P),
         Spacer(1, 8),
         table([["Governance indicator", y, py],
                ["Supervisory board members", c["board"], p["board"]],
                ["Independent directors", c["indep"], p["indep"]],
                ["Women on the board", c["wboard"], p["wboard"]],
                ["Confirmed incidents of corruption", c["corr"], p["corr"]]]),
         Spacer(1, 10),
         Paragraph("Remuneration report (extract)", H2),
         table([["Remuneration", "Unit", y, py],
                ["CEO total remuneration", "EUR thousand", c["ceo"], p["ceo"]]])]
    SimpleDocTemplate(str(HERE / f"nordwind_{year}.pdf"), pagesize=A4, title=f"Nordwind {year}").build(s)


def _scanned_governance_png() -> Path:
    tmp = HERE / "_gov_tmp.pdf"
    s = [Paragraph("Board and governance", H2),
         Paragraph("The Board comprises 9 directors, of whom 4 are independent non-executive directors "
                   "and 3 are women. The Chair is independent on appointment; the roles of Chair and "
                   "Chief Executive are combined in Ms. Laura Pike, Executive Chair and CEO, supported by a "
                   "Senior Independent Director.", P),
         Paragraph("The Board operates an Audit & Risk Committee and a Remuneration Committee. "
                   "In 2025 there were no confirmed incidents of corruption.", P),
         Paragraph("CEO total remuneration for 2025 was GBP 1,980,000.", P)]
    SimpleDocTemplate(str(tmp), pagesize=A4).build(s)
    img = pdfium.PdfDocument(str(tmp))[0].render(scale=2.2).to_pil().convert("L")
    png = HERE / "_gov_scan.png"
    img.save(png)
    tmp.unlink()
    return png


def helios():
    png = _scanned_governance_png()
    s = [Paragraph("Helios Components plc", H1), Paragraph("Annual Report 2025 - Strategic Report", H2),
         Paragraph("Helios designs and manufactures precision components for the aerospace and energy "
                   "sectors from five plants in the UK and Poland. We employed 3,215 people at "
                   "31 December 2025 (2024: 3,050).", P),
         Paragraph("Our ambition is to become net zero by 2040. Since 2019 we have reduced Scope 1 "
                   "emissions by 35% and we target a further 50% reduction by 2030.", P), PageBreak(),

         Paragraph("Environmental performance", H2),
         Paragraph("Our Environmental Policy, approved by the Board, covers energy, emissions, water and waste, "
                   "and all plants operate under ISO 14001.", P),
         table([["GHG emissions", "Unit", "2024", "2025"],
                ["Scope 1", "ktCO2e", "22.4", "21.1"],
                ["Scope 2 - market-based", "ktCO2e", "8.9", "6.2"],
                ["Scope 3", "ktCO2e", "160.0", "151.3"],
                ["Total GHG emissions", "ktCO2e", "191.3", "196.0"]]),
         Spacer(1, 8),
         Paragraph("Note: total 2025 emissions reflect restated Scope 3 category 1 data.", P),
         Spacer(1, 8),
         table([["Water (megalitres)", "Unit", "2024", "2025"],
                ["Water withdrawal", "ML", "612", "598"],
                ["Water discharge", "ML", "455", "441"],
                ["Water consumption", "ML", "157", "157"]]),
         PageBreak(),

         Paragraph("People and culture", H2),
         Paragraph("Women make up 24% of our workforce. Attrition rate for the year was 9.6% "
                   "(2024: 11.2%). We hired 402 new employees. Average training hours per employee rose "
                   "to 22 hours. LTIFR was 1.8 and there were no work-related fatalities.", P),
         Paragraph("Our Modern Slavery Statement and Human Rights Policy apply to all employees and "
                   "suppliers.", P),
         PageBreak(),
         Image(str(png), width=A4[0] - 4 * cm, height=(A4[0] - 4 * cm) * 1.414)]
    SimpleDocTemplate(str(HERE / "helios_2025.pdf"), pagesize=A4, title="Helios 2025").build(s)
    png.unlink()


if __name__ == "__main__":
    nordwind(2025)
    nordwind(2024)
    helios()
    (HERE / "manifest.csv").write_text(
        "company,year,pdf\nNordwind Logistics AG,2025,nordwind_2025.pdf\n"
        "Nordwind Logistics AG,2024,nordwind_2024.pdf\nHelios Components plc,2025,helios_2025.pdf\n")
    print("samples written")
