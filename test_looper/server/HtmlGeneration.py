"""
HtmlGeneration

Simple utilities for generating HTML for the TestLooperHttpServer.
"""

import logging
import re

headers = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/3.3.6/css/bootstrap.min.css"
      integrity="sha384-1q8mTJOASx8j1Au+a5WDVnPi2lkFfwwEAa8hDDdjZlpLegxhjVME1fgjWPGmkzs7"
      crossorigin="anonymous"/>
<link rel="stylesheet" href="/css/test-looper.css"/>
</head>
<body>
<div class="container-fluid">
"""

def render(x):
    if isinstance(x, HtmlElement):
        return x.render()
    return x

class HtmlElement(object):
    """Models an arbitrary string of html that will show up as fixed-width text."""
    def elementList(self):
        return [self]

    def __add__(self, other):
        if isinstance(other, (basestring)):
            return self + HtmlString(other)

        return HtmlElements(self.elementList() + other.elementList())

    def __radd__(self, other):
        if isinstance(other, (basestring)):
            return HtmlString(other) + self

        return HtmlElements(other.elementList() + self.elementList())

    def __len__(self):
        return 0

    def render(self):
        return ""

class ParagraphTag(HtmlElement):
    def __init__(self, contained, mods):
        self.contained = makeHtmlElement(contained)
        self.mods = mods

    def __len__(self):
        return len(self.contained)

    def render(self):
        return ("<p " + " ".join(['%s="%s"' % (k, v) for k, v in self.mods.iteritems()]) + ">" +
                self.contained.render() + "</p>")

class SpanTag(HtmlElement):
    def __init__(self, contained, mods):
        self.contained = makeHtmlElement(contained)
        self.mods = mods

    def __len__(self):
        return len(self.contained)

    def render(self):
        return ("<span " + " ".join(['%s="%s"' % (k,v) for k,v in self.mods.iteritems()]) + ">" +
                self.contained.render() + "</span>")

def makeHtmlElement(elt):
    if isinstance(elt, HtmlElement):
        return elt
    return HtmlString(str(elt))

class HtmlString(HtmlElement):
    def __init__(self, text):
        self.text = text.encode('ascii', 'xmlcharrefreplace')

    def render(self):
        return self.text

    def __len__(self):
        special_symbols = re.findall(r"&\w+;", self.text)
        return len(self.text) + len(special_symbols) - sum(len(s) for s in special_symbols)

class HtmlElements(HtmlElement):
    """Models several concatenated html elements"""
    def __init__(self, elts):
        self.elts = elts
        self.lengthStash = None

    def elementList(self):
        return self.elts

    def render(self):
        return "".join([x.render() for x in self.elts])

    def __len__(self):
        if self.lengthStash is None:
            self.lengthStash = sum([len(x) for x in self.elts])
        return self.lengthStash

class Link(HtmlElement):
    def __init__(self, url, text, hover_text=None, is_button=False):
        self.url = url
        self.text = text
        self.hover_text = hover_text or ''
        self.is_button = is_button

    def __len__(self):
        return len(self.text)

    def render(self):
        button_class = 'class="btn btn-default" role="button"' if self.is_button else ''
        return """<a href="%s" title="%s" %s>%s</a>""" % (
            self.url, self.hover_text, button_class, render(self.text)
            )

    def withTextReplaced(self, newText):
        return Link(self.url, newText, self.hover_text)


whitespace = "&nbsp;"

def emphasize_probability(text, level, direction):
    text = text + whitespace + \
        '<span class="glyphicon glyphicon-asterisk" aria-hidden="true"></span>' * abs(level)
    return greenBacking(text) if direction > 0 else redBacking(text)

def pad(s, length):
    text_length = len(s)
    if text_length < length:
        return s + whitespace  * (length - text_length)
    return s


def link(linkTxt, linkUrl, hover_text=None):
    return Link(linkUrl, linkTxt, hover_text)


def stack(*elements):
    return "".join(elements)

def button(value, linkVal):
    return """
    <form action=\"%s\">
        <input type="submit" value=\"%s\"/>
    </form>
    """ % (linkVal, value)


def elementTextLength(e):
    e = e.render() if isinstance(e, HtmlElement) else str(e)
    text_length = sum(len(s[s.find('>')+1:]) for s in e.split('<'))
    logging.info("Text length: %d, Element: %s", text_length, e)
    return text_length

def grid(rows, header_rows=1):
    """Given a list-of-lists (e.g. row of column values), format as a grid.

    We compute the width of each column (assuming null values if a column
    is not entirely populated).
    """

    rows = [[makeHtmlElement(x) for x in row] for row in rows]

    table_headers = "\n".join(
        "<tr>%s</tr>" % "\n".join('<th class="fit">%s</th>' % h.render()
                                  for h in row)
        for row in rows[:header_rows]
        )
    table_rows = "\n".join(
        "<tr>%s</tr>" % "\n".join('<td class="fit">%s</td>' % c.render() for c in row)
        for row in rows[header_rows:]
        )

    format_str = ('<table class="table table-hscroll table-condensed table-striped">'
                  '{headers}\n{rows}'
                  '</table>')
    return format_str.format(
        headers=table_headers,
        rows=table_rows
        )

def lightGrey(text):
    return ParagraphTag(text, {"class": "text-muted"})

def red(text):
    return ParagraphTag(text, {"class": "text-danger"})

def greenBacking(text):
    return ParagraphTag(text, {"class": "bg-success"})

def redBacking(text):
    return ParagraphTag(text, {"class": "bg-danger"})

def blueBacking(text):
    return ParagraphTag(text, {"class": "bg-info"})

def lightGreyBacking(text):
    return SpanTag(text, {'style': "background-color:#dddddd"})

def errRateAndTestCount(testCount, successCount):
    if testCount == 0:
        return "  0     "

    successCount = float(successCount)

    errRate = 1.0 - successCount / testCount

    if errRate == 0.0:
        return "%4s@%3s%s" % (testCount, 0, "%")

    if errRate < 0.01:
        errRate *= 10000
        errText = '.%2s' % int(errRate)
    elif errRate < 0.1:
        errRate *= 100
        errText = '%s.%s' % (int(errRate), int(errRate * 10) % 10)
    else:
        errRate *= 100
        errText = '%3s' % int(errRate)

    return "%4s@%3s" % (testCount, errText) + "%"


def errRate(frac):
    tr = "%.1f" % (frac * 100) + "%"
    tr = tr.rjust(6)

    if frac < .1:
        tr = lightGrey(tr)

    if frac > .9:
        tr = red(tr)

    return tr

def selectBox(name, items, default=None):
    '''
    items - a list of (value, caption) tuples representing the items in the select box.
    '''
    options = ['<option value="%s" %s>%s</option>' % (v, "selected" if v == default else '', t) \
               for v, t in items]

    return '<select name=%s>%s</select>' % (name, '\n'.join(options))
