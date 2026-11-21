"""The whole screen of a Slowboard 470 (540x960, 16 greys, standing tall): the agenda as the board's
mockup drew it. The weekday large in Alfa Slab One and the date under it, a rule, then one row per
event: the day (on its first row only) in Alfa, the time and the title in DejaVu Sans Condensed
Bold. Important events are inverted (no red on this glass), holidays sit on a light grey band, and
a thin grey line separates the days. The rows stop above the board's app strip (y 900).

One card: the calendar has nothing to tap, so the board's keys only move between apps and there
are no key hints to draw. The body runs to the right margin. On other g16 sizes the same layout is
scaled.
"""
from datetime import date

from PIL import Image, ImageDraw

import slowapp_kit as kit
from slowapp_kit import grey, snap_g16

from . import rules
from .draw import day_name, ellipsize, rows_for

STRIP_Y = 900                       # the board's app strip on a 540x960 (sb_proto areas::STRIP)
INK, SOFT, RULE, BAND, SEP = 0, 4, 0, 13, 12   # grey levels: text, the date, the head rule, holidays, day lines


def _fonts(s):
    alfa = lambda n: kit.font("AlfaSlabOne-Regular.ttf", max(8, round(n * s)))          # noqa: E731
    cond = lambda n, b=True: kit.font("DejaVuSansCondensed-Bold.ttf" if b else "DejaVuSansCondensed.ttf",
                                      max(8, round(n * s)))                              # noqa: E731
    return dict(day=alfa(52), date=cond(27), row_day=alfa(26), time=cond(23), allday=cond(20, False),
                title=cond(28), msg=cond(30), sub=cond(22, False))


class Layout:
    def __init__(self, w, h):
        self.s = s = min(w / 540, h / 960)
        self.w, self.h = w, h
        self.left, self.right = round(36 * s), w - round(36 * s)
        self.band_left, self.band_right = round(24 * s), w - round(24 * s)
        self.top = round(152 * s)
        self.bottom = (STRIP_Y if (w, h) == (540, 960) else h) - round(8 * s)
        self.row_h, self.band_h, self.gap = round(46 * s), round(52 * s), round(6 * s)
        self.col_day, self.col_time = round(118 * s), round(86 * s)


def rows_470(agenda, now):
    """The agenda's rows for the full screen: today's holidays first (on the list, as a band, rather
    than a tag by a heading this card doesn't have), then every row as the slot shows them."""
    banners, rows = rows_for(agenda, now)
    today = now.date().isoformat()
    head = [dict(e=dict(date=today, title=b, start=None, end=None), hi=False, holiday=True) for b in banners]
    return head + rows


def screen(surface, agenda, now):
    """-> the home card's image for `agenda` (None: no calendar connected) at `now`."""
    w, h = surface.w, surface.h
    L, f = Layout(w, h), _fonts(min(w / 540, h / 960))
    img = Image.new("RGB", (w, h), grey(15))
    d = ImageDraw.Draw(img)
    s = L.s
    d.text((L.left, round(30 * s)), f"{now:%A}", font=f["day"], fill=grey(INK), anchor="lt")
    d.text((L.left + round(2 * s), round(92 * s)), f"{now.day} {now:%b}", font=f["date"], fill=grey(SOFT), anchor="lt")
    d.rectangle((L.left, round(136 * s), L.right - 1, round(136 * s) + max(2, round(3 * s)) - 1), fill=grey(RULE))
    if agenda is None or not (rows := rows_470(agenda, now)):
        msg, sub = ("Connect a calendar", "on the Calendar app's page") if agenda is None else ("Nothing coming up", "")
        mid = (L.top + L.bottom) // 2
        d.text((w // 2, mid - round(16 * s)), msg, font=f["msg"], fill=grey(INK), anchor="mm")
        if sub:
            d.text((w // 2, mid + round(22 * s)), sub, font=f["sub"], fill=grey(SOFT), anchor="mm")
        return snap_g16(img)
    today = now.date()
    y, last = L.top, None
    # The day column as wide as this card's longest day name ("Wed 28") and a gap, at least the mockup's.
    widest = max(d.textlength(day_name(date.fromisoformat(r["e"]["date"]), today), font=f["row_day"]) for r in rows)
    x_time = L.left + max(L.col_day, round(widest + 14 * s))
    x_title = x_time + L.col_time
    for r in rows:
        e = r["e"]
        day = date.fromisoformat(e["date"])
        band = r["hi"] or r["holiday"]
        new_day = last is not None and day != last
        top = y + (L.gap if new_day else 0)
        hgt = L.band_h if band else L.row_h
        if top + hgt > L.bottom:
            break
        if new_day:
            d.rectangle((L.left, y + 1, L.right - 1, y + 2), fill=grey(SEP))
        if band:
            d.rectangle((L.band_left, top, L.band_right - 1, top + hgt - 1), fill=grey(INK if r["hi"] else BAND))
        fg = grey(15 if r["hi"] else INK)
        cy = top + hgt // 2
        if day != last:
            d.text((L.left, cy), day_name(day, today), font=f["row_day"], fill=fg, anchor="lm")
        if e.get("start"):
            d.text((x_time, cy), e["start"], font=f["time"], fill=fg, anchor="lm")
        else:
            d.text((x_time, cy), "all day", font=f["allday"], fill=fg, anchor="lm")
        title = ellipsize(d, rules.display_title(e["title"]), f["title"], L.right - x_title)
        d.text((x_title, cy), title, font=f["title"], fill=fg, anchor="lm")
        y, last = top + hgt, day
    return snap_g16(img)


def cards(surface, agenda, now):
    """-> [(id, image, title, peek)]: one home card."""
    if agenda is None:
        return [("home", screen(surface, None, now), "Calendar", "No calendar connected")]
    nxt = next((r["e"] for r in rows_for(agenda, now)[1] if not r["holiday"]), None)
    peek = ""
    if nxt:
        peek = f"{day_name(date.fromisoformat(nxt['date']), now.date())} {nxt['start'] or 'all day'} " \
               f"{rules.display_title(nxt['title'])}"[:60]
    return [("home", screen(surface, agenda, now), "Calendar", peek)]
