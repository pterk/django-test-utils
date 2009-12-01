import cgi
import urlparse
import logging

from BeautifulSoup import BeautifulSoup

from django.conf import settings
from django.db import transaction
from django.test.client import Client
from django.test.utils import setup_test_environment, teardown_test_environment

from test_utils.crawler import signals as test_signals
from test_utils.crawler.plugins.base import Plugin

crawl_logger = logging.getLogger('crawler')

class Crawler(object):
    """
    This is a class that represents a URL crawler in python
    """
    def __init__(self, base_url, conf_urls={}, verbosity=1, **kwargs):
        self.base_url = base_url
        self.conf_urls = conf_urls
        self.verbosity = verbosity

        #These two are what keep track of what to crawl and what has been.
        self.not_crawled = [('START',self.base_url)]
        self.crawled = {}

        self.c = Client(REMOTE_ADDR='127.0.0.1')

        self.plugins = []
        for plug in Plugin.__subclasses__():
            active = getattr(plug, 'active', True)
            if active:
                self.plugins.append(plug())

    def _parse_urls(self, url, resp):
        parsed = urlparse.urlparse(url)
        soup = BeautifulSoup(resp.content.decode("utf-8"))
        returned_urls = []
        hrefs = [a['href'] for a in soup('a') if a.has_key('href')]
        for a in hrefs:
            parsed_href = urlparse.urlparse(a)
            if parsed_href.path.startswith('/') and not parsed_href.scheme:
                returned_urls.append(a)
            elif not parsed_href.scheme:
                #Relative path = previous path + new path
                returned_urls.append(parsed.path + a)
        return returned_urls

    def get_url(self, from_url, to_url):
        """
        Takes a url, and returns it with a list of links
        This uses the Django test client.
        """
        parsed = urlparse.urlparse(to_url)
        request_dict = dict(cgi.parse_qsl(parsed.query))
        url_path = parsed.path
        #url_path now contains the path, request_dict contains get params

        crawl_logger.info("%s: link to %s with parameters %s", from_url, to_url, request_dict)

        test_signals.pre_request.send(self, url=to_url, request_dict=request_dict)
        resp = self.c.get(url_path, request_dict, follow=True)
        test_signals.post_request.send(self, url=to_url, response=resp)
        returned_urls = self._parse_urls(to_url, resp)
        test_signals.urls_parsed.send(self, fro=to_url, returned_urls=returned_urls)
        return (resp, returned_urls)

    def run(self):

        old_DEBUG = settings.DEBUG
        settings.DEBUG = False

        setup_test_environment()
        test_signals.start_run.send(self)

        while self.not_crawled:
            #Take top off not_crawled and evaluate it
            from_url, to_url = self.not_crawled.pop(0)

            transaction.enter_transaction_management()
            try:
                resp, returned_urls = self.get_url(from_url, to_url)
            except Exception, e:
                crawl_logger.exception("%s had unhandled exception: %s", to_url, e)
                continue
            finally:
                transaction.rollback()

            self.crawled[to_url] = True
            #Find its links that haven't been crawled
            for base_url in returned_urls:
                if base_url not in [to for fro,to in self.not_crawled] and not self.crawled.has_key(base_url):
                    self.not_crawled.append((to_url, base_url))

        test_signals.finish_run.send(self)

        teardown_test_environment()

        settings.DEBUG = old_DEBUG
