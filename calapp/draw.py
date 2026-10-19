"""The agenda card in the panel's inks, drawn as the legacy dashboard's right third drew its agenda
(epaper-dashboard renderer/weather.py, build()): an "Upcoming" heading in Alfa Slab One, then one
row per event, the day, the time and the title in DejaVu Sans Condensed. Important events are red
with a red bar; today's holiday is a yellow tag by the heading, and a coming holiday a yellow row.
Hidden events are left off, and so are ones that have ended.

Text is drawn without antialiasing (fontmode "1"): every pixel must be one of the inks.
"""
from datetime import date, datetime, timedelta

from PIL import Image, ImageDraw

import slowapp_kit as kit
from slowapp_kit import BLACK, RED, WHITE, YELLOW

from . import rules

TOP, ROW_H = 48, 28
X_DAY, GAP = 13, 8                             # from the left margin, as the legacy right third


def serif(size):
    return kit.font("AlfaSlabOne-Regular.ttf", size)


def condensed(size, bold=False):
    return kit.font("DejaVuSansCondensed-Bold.ttf" if bold else "DejaVuSansCondensed.ttf", size)


def bold(size):
    return kit.font("DejaVuSans-Bold.ttf", size)


def ellipsize(d, text, fnt, width):
    if d.textlength(text, font=fnt) <= width:
        return text
    while text and d.textlength(text + "…", font=fnt) > width:
        text = text[:-1]
    return text.rstrip(" -:") + "…"


def day_name(day, today):
    n = (day - today).days
    return "Today" if n == 0 else "Tmrw" if n == 1 else f"{day:%a} {day.day}"


def rows_for(agenda, now):
    """-> (today's holiday names, [row]) where a row is {"e", "hi", "holiday"} in time order. now: the
    run's time, aware; ended events and hidden ones are dropped (renderer/events.py's classify)."""
    local = now.replace(tzinfo=None)
    banners, picked = rules.classify(agenda, local)
    today = local.date().isoformat()
    out = [dict(e=e, hi=hi, holiday=False) for e, _, _, hi in picked if e["date"] >= today]   # an agenda from an earlier day
    out += [dict(e=e, hi=False, holiday=True) for e in agenda
            if e.get("cal") == "holiday" and e["date"] > today]
    out.sort(key=lambda r: (r["e"]["date"], not r["holiday"], r["e"]["start"] or ""))
    return banners, out


def per_card(h):
    return (h - TOP) // ROW_H


def card(surface, heading, rows, now, banner=None, empty=None):
    w, h = surface.w, surface.h
    img = Image.new("RGB", (w, h), WHITE)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    x0, x1 = 12, w - 14
    d.text((x0, 2), heading, font=serif(30), fill=BLACK)
    if banner:
        hx = x0 + d.textlength(heading, font=serif(30)) + 16
        tag = ellipsize(d, banner, bold(13), x1 - hx - 16)
        if tag:
            l, t, r, b = d.textbbox((hx + 8, 22), tag, font=bold(13), anchor="lm")
            d.rounded_rectangle((hx, t - 5, r + 8, b + 5), 7, fill=YELLOW, outline=BLACK)
            d.text((hx + 8, 22), tag, font=bold(13), fill=BLACK, anchor="lm")
    if not rows:
        msg, sub = empty or ("Nothing coming up", "")
        d.text((w // 2, h // 2 - 12), msg, font=condensed(22, True), fill=BLACK, anchor="mm")
        if sub:
            d.text((w // 2, h // 2 + 16), sub, font=condensed(16), fill=BLACK, anchor="mm")
        return img
    today = now.date()
    # Columns as wide as this card's longest day name ("Wed 28") and a time ("18:00"); the legacy
    # third's fixed 88 / 146 let "Wed 28" touch its time.
    x_time = X_DAY + GAP + max(int(d.textlength(day_name(date.fromisoformat(r["e"]["date"]), today), font=serif(18)))
                               for r in rows)
    x_title = x_time + GAP + int(d.textlength("00:00", font=condensed(18, True)))
    last_day = None
    for i, r in enumerate(rows):
        e, y = r["e"], TOP + i * ROW_H
        colour = RED if r["hi"] else BLACK
        day = date.fromisoformat(e["date"])
        if day != last_day:
            if last_day is not None:
                d.line((x0, y - 3, x1, y - 3), fill=BLACK, width=1)
            d.text((x0 + X_DAY, y + 11), day_name(day, today), font=serif(18), fill=BLACK, anchor="lm")
            last_day = day
        tf = condensed(19, True)
        title = rules.display_title(e["title"])
        if r["holiday"]:
            text = ellipsize(d, title, bold(13), x1 - x0 - x_time - 16)
            l, t, rr, b = d.textbbox((x0 + x_time + 8, y + 11), text, font=bold(13), anchor="lm")
            d.rounded_rectangle((x0 + x_time, t - 5, rr + 8, b + 5), 7, fill=YELLOW, outline=BLACK)
            d.text((x0 + x_time + 8, y + 11), text, font=bold(13), fill=BLACK, anchor="lm")
            continue
        if r["hi"]:
            d.rectangle((x0, y, x0 + 5, y + 21), fill=RED)
        if e["start"]:
            d.text((x0 + x_time, y + 11), e["start"], font=condensed(18, True), fill=colour, anchor="lm")
        else:                                  # "all day" in the time column's width
            d.text((x0 + x_time, y + 11), "all day", font=condensed(13, True), fill=colour, anchor="lm")
        d.text((x0 + x_title, y + 11), ellipsize(d, title, tf, x1 - x0 - x_title), font=tf, fill=colour, anchor="lm")
    return img


def cards(surface, agenda, now):
    """-> [(id, image, title, peek)]: the home card, and "later" when the rows run over."""
    if agenda is None:
        return [("home", card(surface, "Upcoming", [], now, empty=("Connect a calendar", "on the Calendar app's page")), "Upcoming",
                 "No calendar connected")]
    banners, rows = rows_for(agenda, now)
    n = per_card(surface.h)
    banner = banners[0] if banners else None
    home = rows[:n]
    nxt = next((r["e"] for r in rows if not r["holiday"]), None)
    peek = ""
    if nxt:
        peek = f"{day_name(date.fromisoformat(nxt['date']), now.date())} {nxt['start'] or 'all day'} " \
               f"{rules.display_title(nxt['title'])}"[:60]
    out = [("home", card(surface, "Upcoming", home, now, banner), "Upcoming", peek)]
    if len(rows) > n:
        out.append(("later", card(surface, "Later", rows[n:2 * n], now), "Later", ""))
    return out


def next_change(agenda, now):
    """When the card next changes by itself: the next end of a shown event today, else midnight."""
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), now.tzinfo)
    best = midnight
    for e in agenda or []:
        if e.get("cal") == "holiday" or e.get("tag") == "hidden" or not e.get("end"):
            continue
        if e["date"] == now.date().isoformat():
            t = datetime.combine(now.date(), datetime.strptime(e["end"], "%H:%M").time(), now.tzinfo)
            if now < t < best:
                best = t
    return max(best, now + timedelta(minutes=5))
