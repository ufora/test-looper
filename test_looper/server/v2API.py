import logging
import cherrypy
import os

class v2API(object):
    exposed = True
    def __init__(self, authenticatationCallback):
        self.authenticatationCallback = authenticatationCallback

    @staticmethod
    def readWorkerFromRequestBody():
        try:
            rawBody = cherrypy.request.body.read(
                int(cherrypy.request.headers['Content-Length'])
                )
            return rawBody
        except ValueError:
            raise cherrypy.HTTPError(status=400, message="Error")

    def GET(self, *route):
        self.authenticatationCallback()
        logging.info("Route: %s", route)
        thisDir = os.path.dirname(__file__)
        if len(route) == 0:
            filepath = os.path.join(thisDir, 'root.html')
        else:
            return ""
        
        logging.info("Get request. Full path %s", filepath)
        htmlFile = open(filepath, "r")
        result = htmlFile.read()
        htmlFile.close()

        return result

    def POST(self):
        logging.info("Post request")
        return "Post request!"
    