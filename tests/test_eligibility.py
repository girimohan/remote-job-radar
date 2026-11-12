import pytest

from radar.eligibility import classify
from radar.models import Job


def job(location="", description="", remote=None, restrictions=None, title="Software Engineer"):
    return Job("test", "1", "Acme", title, "https://example.com", location=location, description=description,
               remote=remote, restrictions=restrictions)


@pytest.mark.parametrize("location,expected", [
    ("Helsinki, Finland", "finland"),
    ("Remote - Nordics", "finland"),
    ("Remote - Europe", "europe"),
    ("Remote EMEA", "europe"),
    ("EU (Remote)", "europe"),
    ("Remote, US or Europe", "europe"),
    ("Home Based - Americas; Home based - EMEA", "europe"),
    ("Remote (Worldwide)", "worldwide"),
    ("Anywhere in the World", "worldwide"),
    ("Remote, United States", "us_only"),
    ("Remote - US", "us_only"),
    ("Remote - Americas", "us_only"),
    ("Remote - CA", "us_only"),
    ("ET/CT/PT Remote", "us_only"),
    ("Remote, Austin, TX", "us_only"),
    ("Remote; London, UK", "eu_country"),
    ("Remote, Germany", "eu_country"),
    ("Remote (Canada)", "other_region"),
    ("India (Remote), New Delhi, India", "other_region"),
    ("Remote - Mississauga", "other_region"),
    ("Remote", "unclear"),
    ("", "not_remote"),
    ("San Francisco, CA", "not_remote"),
])
def test_location_rules(location, expected):
    assert classify(job(location))[0] == expected


def test_board_regions_with_remote_flag():
    # Remote-only boards give a region label and no "remote" word.
    assert classify(job("USA Only", remote=True))[0] == "us_only"
    assert classify(job("Europe Only", remote=True))[0] == "europe"
    assert classify(job("Latin America Only", remote=True))[0] == "other_region"


def test_indiana_is_not_india():
    # The bug that put 1,200 Indiana jobs in India in the dataset this project started from.
    assert classify(job("Remote - Indianapolis, IN"))[0] == "us_only"
    assert classify(job("Remote, Bloomington, Indiana, United States"))[0] == "us_only"
    assert classify(job("Remote - Indian Trail, NC"))[0] == "us_only"


def test_us_city_named_after_european_one():
    assert classify(job("Remote, Berlin, CT, USA"))[0] == "us_only"
    assert classify(job("Remote - Berlin"))[0] == "eu_country"


def test_description_fills_in_bare_remote():
    assert classify(job("Remote", "You must be based in the United States."))[0] == "us_only"
    assert classify(job("Remote", "Applicants must be authorized to work in the US."))[0] == "us_only"
    assert classify(job("Remote", "We hire across EMEA."))[0] == "europe"
    assert classify(job("Remote", "Overlap with CET working hours expected."))[0] == "europe"
    assert classify(job("Remote", "Work from anywhere, we are async."))[0] == "worldwide"


def test_description_mentions_are_not_restrictions():
    # Talking about customers or offices is not the same as saying where you may work from.
    assert classify(job("Remote", "Our customers in Europe and the US love us."))[0] == "unclear"
    assert classify(job("Remote", "We are the fastest growing startup in the US, with customers worldwide."))[0] == "unclear"
    assert classify(job("Remote", "Contact us only via the form."))[0] == "unclear"


def test_worldwide_overridden_by_us_requirement():
    label, reason = classify(job("Worldwide", "This role is US only due to compliance."))
    assert label == "us_only" and "description" in reason


def test_explicit_country_list():
    assert classify(job("x", remote=True, restrictions=["United States"]))[0] == "us_only"
    assert classify(job("x", remote=True, restrictions=["United States", "Canada"]))[0] == "us_only"
    assert classify(job("x", remote=True, restrictions=["Germany", "Finland", "Spain"]))[0] == "finland"
    assert classify(job("x", remote=True, restrictions=["Germany", "Spain"]))[0] == "eu_country"
    europe_wide = ["Austria", "Belgium", "Denmark", "Estonia", "Finland", "France", "Germany", "Sweden"]
    assert classify(job("x", remote=True, restrictions=europe_wide))[0] == "europe"
    assert classify(job("x", remote=True, restrictions=["Nigeria"]))[0] == "other_region"
    assert classify(job("x", remote=True, restrictions=["Brazil"]))[0] == "other_region"
    assert classify(job("Worldwide", remote=True, restrictions=[]))[0] == "worldwide"


def test_remote_flag_from_source_counts():
    assert classify(job("Europe", remote=True))[0] == "europe"
    assert classify(job("Europe", remote=None))[0] == "not_remote"
