import urllib

import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.ComboContexts as ComboContexts

octicon = HtmlGeneration.octicon

class Context(object):
    def __init__(self, renderer, options):
        self.renderer = renderer
        self.testManager = renderer.testManager
        self.database = renderer.testManager.database
        self.options = options

    def __cmp__(self, other):
        return cmp(self.primaryObject(), other.primaryObject())

    @staticmethod
    def popToDash(items):
        try:
            ix = items.index("-")
            return items[:ix], items[ix+1:]
        except ValueError:
            return items, []

    def redirect(self):
        return self.renderer.redirect()        

    def urlBase(self):
        assert False, "Subclasses Implement"

    def primaryObject(self):
        assert False, "Subclasses Implement"

    def urlString(self, **kwargs):
        finalArgs = dict(self.options)
        finalArgs.update(kwargs)
        for k in kwargs:
            if kwargs[k] is None:
                del finalArgs[k]

        return "/" + self.urlBase() + ("?" + urllib.urlencode(finalArgs) if finalArgs else "")

    def renderNavbarLink(self):
        return self.renderLink()

    def renderMenuItemText(self, isHeader):
        assert False, "Subclasses Implement: %s" % type(self)

    def renderBreadcrumbPrefixes(self):
        return []

    def childContexts(self, curContext):
        return None

    def renderPostViewSelector(self):
        return ""

    def contextViews(self):
        return []

    def renderViewMenuItem(self, viewName):
        return viewName

    def currentView(self):
        return self.options.get('view', self.defaultView())

    def defaultView(self):
        return self.contextViews()[0]

    def renderViewMenuMouseoverText(self, viewName):
        return ""

    def renderMenuItemTitle(self, isHeader):
        return ""

    def renderPageHeader(self):
        headers = []

        curContext = self

        while curContext.parentContext():
            parent = curContext.parentContext()

            children = parent.childContexts(curContext)

            if curContext == self:
                children = [c.withOptions(**self.options) for c in children]

            if children:
                dd_items = [
                    '<a class="dropdown-item{active}" href="{link}" title="{title}">{contents}</a>'.format(
                        active=" active" if child == curContext else "", 
                        link=child.urlString(),
                        contents=child.renderMenuItemText(isHeader=False),
                        title=child.renderMenuItemTitle(isHeader=False)
                        )
                    for child in children
                    ]

                item = """<div class="btn-group">
                  <a role="button" href="{url}" class="btn btn-xs {btnstyle}" title="{title}">{elt}</a>
                  <button class="btn btn-xs {btnstyle} dropdown-toggle dropdown-toggle-split" type="button" id="dropdownMenuButton" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false">
                  </button>
                  <div class="dropdown-menu" aria-labelledby="dropdownMenuButton">
                    {dd_items}
                  </div>
                  
                </div>
                """.format(
                    url=curContext.withOptionsReset(view=curContext.options.get("view")).urlString(),
                    elt=curContext.renderMenuItemText(isHeader=True),
                    title=curContext.renderMenuItemTitle(isHeader=True),
                    dd_items = "".join(dd_items),
                    btnstyle="btn-outline-secondary"
                    )
            else:
                item = curContext.renderNavbarLink()

            if not isinstance(item, str):
                item = item.render()

            headers = [item] + ['<span class="px-1">&#x2F;</span>' if headers else ""] + headers

            for preItem in reversed(curContext.renderBreadcrumbPrefixes()):
                headers = [preItem, '<span class="px-1">&#x2F;</span>'] + headers

            curContext = parent

        if self.options.get("testGroup"):
            headers += ['<span class="px-1">&#x2F;</span>'] + [octicon("beaker") + self.options.get("testGroup")]
        else:
            if self.contextViews():
                headers = headers + ['<span class="px-4">::</span>']
                buttons = []
                curView = self.options.get("view", self.defaultView())
                    
                for view in self.contextViews():
                    buttons.append(
                        '<a role="button" href="{url}" title="{title}" class="btn btn-xs {btnstyle}">{elt}</a>'.format(
                            url=self.contextFor(self.primaryObject(), view=view).urlString(),
                            elt=self.renderViewMenuItem(view),
                            title=self.renderMenuItemTitle(view),
                            btnstyle="btn-primary" if view == curView else "btn-outline-secondary"
                            )
                        )

                headers = headers + [
                    """<div class="btn-group" role="group">{buttons}</div>"""
                        .format(buttons="".join(buttons))
                    ]

        postfix = self.renderPostViewSelector()
        
        headers = ["<span class='tl-navbar-item'>%s</span>" % h for h in headers]

        headers.append("""
            <span class="tl-navbar-fill">
                <span class="tl-navbar tl-navbar-fromright">
                    <span class="tl-navbar-item">
                        <span class="tl-navbar tl-navbar">
                            <span class="tl-navbar-item">{rightside}</span>
                        </span>
                    </span>
                    <span class="tl-navbar-fill">
                        <span class="tl-navbar tl-navbar-centered">
                            <span class="tl-navbar-item">{postfix}</span>
                        </span>
                    </span>
                </span>
            </span>"""
                .format(postfix=postfix, rightside=self.renderer.reload_link().render())
            )

        return '<div class="p-2 bg-light mr-auto tl-navbar">%s</div>' % "".join(headers)

    def renderWholePage(self):
        if self.options.get("bodyOnly"):
            return self.renderPageBody()
        
        return (
            HtmlGeneration.headers + 
            self.renderPageHeader() + 
            '<main class="py-md-2"><div class="container-fluid">' + 
            (card("Invalid Object") if not self.primaryObject() else 
                    self.renderPageBody()) +
            "</div></main>" + 
            HtmlGeneration.footers
            )

    def contextFor(self, entity, **kwargs):
        return self.renderer.contextFor(entity, kwargs)

    def withOptionsReset(self, **options):
        options = {k:v for k,v in options.iteritems() if v is not None}
        return self.renderer.contextFor(self.primaryObject(), options)

    def withOptions(self, **kwargs):
        options = dict(self.options)
        options.update(kwargs)

        options = {k:v for k,v in options.iteritems() if v is not None}

        return self.renderer.contextFor(self.primaryObject(), options)

    