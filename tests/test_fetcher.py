"""The polite fetcher: robots.txt, rate limit, page cap, own-site only, clear user agent (fake network only)."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import ssl
import unittest
import urllib.error

from fictional_source import BASE, FakeClock, FakeWeb, saved_pages
from grant_platform.services import fetcher as fetching
from grant_platform.services.fetcher import FetchError, PageLimitReached, PoliteFetcher, RobotsError


def make(web, **options):
    clock = FakeClock()
    options.setdefault("agent", fetching.user_agent("test@example.org"))
    instance = PoliteFetcher(BASE, opener=web, clock=clock.clock, sleep=clock.sleep, now=lambda: "2026-10-29T08:00:00Z", **options)
    return instance, clock


class FetcherTests(unittest.TestCase):
    def test_reads_robots_first_and_returns_page_text_as_data(self):
        web = FakeWeb()
        instance, _ = make(web)
        page = instance.get("/grants")
        self.assertEqual([url for url, _ in web.requests], [f"{BASE}/robots.txt", f"{BASE}/grants"])
        self.assertIn("Youth Skills Fund", page.text)
        self.assertEqual((page.url, page.status, page.fetched_at), (f"{BASE}/grants", 200, "2026-10-29T08:00:00Z"))
        self.assertEqual(instance.pages_fetched, 1)

    def test_waits_between_every_request(self):
        instance, clock = make(FakeWeb(), min_interval=5)
        instance.get("/grants")
        instance.get("/grants/youth-skills-fund")
        self.assertEqual(clock.slept, [5.0, 5.0])

    def test_minimum_interval_is_never_below_one_second(self):
        instance, _ = make(FakeWeb(), min_interval=0)
        self.assertEqual(instance.min_interval, 1.0)

    def test_a_longer_crawl_delay_in_robots_wins(self):
        instance, clock = make(FakeWeb(saved_pages(robots="User-agent: *\nCrawl-delay: 12\n")), min_interval=5)
        instance.get("/grants")
        instance.get("/grants/youth-skills-fund")
        self.assertEqual(clock.slept[-1], 12.0)

    def test_robots_disallow_stops_before_the_page_is_requested(self):
        web = FakeWeb(saved_pages(robots="User-agent: *\nDisallow: /grants\n"))
        instance, _ = make(web)
        with self.assertRaises(RobotsError):
            instance.get("/grants")
        self.assertEqual([url for url, _ in web.requests], [f"{BASE}/robots.txt"])

    def test_unreadable_robots_stops_the_run(self):
        for failure in (500, 403):
            pages = saved_pages()
            pages[f"{BASE}/robots.txt"] = failure
            instance, _ = make(FakeWeb(pages))
            with self.assertRaises(RobotsError, msg=failure):
                instance.get("/grants")
        instance, _ = make(FakeWeb(down=True))
        with self.assertRaises(RobotsError):
            instance.get("/grants")

    def test_missing_robots_means_everything_is_allowed(self):
        instance, _ = make(FakeWeb(saved_pages(robots=None)))
        self.assertIn("Open grants", instance.get("/grants").text)

    def test_network_errors_name_their_real_cause(self):
        certificate = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        certificate.verify_message = "unable to get local issuer certificate"
        for failure, expected in ((urllib.error.URLError(certificate), "SSLCertVerificationError: unable to get local issuer certificate"),
                                  (urllib.error.URLError(ConnectionRefusedError()), "ConnectionRefusedError"),
                                  (TimeoutError(), "TimeoutError")):
            def broken(request, timeout, failure=failure):
                raise failure
            with self.assertRaises(FetchError) as caught:
                make(broken)[0].get("/grants")
            self.assertIn(expected, str(caught.exception))

    def test_certificate_and_host_checking_stay_on(self):
        context = fetching._tls_context()
        self.assertEqual((context.verify_mode, context.check_hostname), (ssl.CERT_REQUIRED, True))

    def test_redirects_are_accepted_only_to_the_exact_approved_hosts(self):
        other = "https://meadowbank.example.org.evil.example"
        alias = "https://www2.meadowbank.example.org"
        web = FakeWeb(redirects={f"{BASE}/grants": f"{alias}/grants"})
        with self.assertRaises(FetchError):
            make(web)[0].get("/grants")
        web = FakeWeb(redirects={f"{BASE}/grants": f"{alias}/grants"})
        instance, _ = make(web, redirect_hosts=("www2.meadowbank.example.org",))
        self.assertEqual(instance.get("/grants").status, 200)
        for target in (other, "https://example.org", "https://sub.www2.meadowbank.example.org"):
            web = FakeWeb(redirects={f"{BASE}/grants": f"{target}/grants"})
            with self.assertRaises(FetchError):
                make(web, redirect_hosts=("www2.meadowbank.example.org",))[0].get("/grants")
        # pages are still only requested from the source's own host
        with self.assertRaises(FetchError):
            make(FakeWeb(), redirect_hosts=("www2.meadowbank.example.org",))[0].get(f"{alias}/grants")

    def test_page_cap(self):
        instance, _ = make(FakeWeb(), max_pages=1)
        instance.get("/grants")
        with self.assertRaises(PageLimitReached):
            instance.get("/grants/youth-skills-fund")

    def test_only_the_sources_own_site_is_fetched(self):
        web = FakeWeb()
        instance, _ = make(web)
        for url in ("https://elsewhere.example.com/grants", "http://meadowbank.example.org/grants"):
            with self.assertRaises(FetchError, msg=url):
                instance.get(url)
        self.assertEqual(web.requests, [])

    def test_redirect_off_site_is_refused(self):
        web = FakeWeb(redirects={f"{BASE}/grants": "https://elsewhere.example.com/landing"})
        instance, _ = make(web)
        with self.assertRaises(FetchError):
            instance.get("/grants")

    def test_http_errors_carry_the_status(self):
        instance, _ = make(FakeWeb())
        with self.assertRaises(FetchError) as caught:
            instance.get("/grants/no-such-grant")
        self.assertEqual(caught.exception.status, 404)

    def test_user_agent_names_the_platform_and_contact(self):
        web = FakeWeb()
        instance, _ = make(web)
        instance.get("/grants")
        agents = {agent for _, agent in web.requests}
        self.assertEqual(len(agents), 1)
        agent = agents.pop()
        self.assertTrue(agent.startswith("GrantManagementPlatform/"))
        self.assertIn("contact: test@example.org", agent)
        self.assertNotIn("contact", fetching.user_agent(""))

    def test_base_address_must_be_https(self):
        with self.assertRaises(ValueError):
            PoliteFetcher("http://meadowbank.example.org")


if __name__ == "__main__":
    unittest.main()
