"""Pydantic v2 data models for all 7 relational tables and Markdown YAML frontmatter."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


def get_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------------------
# 1. RESIDENCY & MARKET RULES
# ------------------------------------------------------------------------------
class ResidencyRuleItem(BaseModel):
    country: str = Field(..., description="Country primary key")
    residency_program_name: Optional[str] = None
    min_investment_local: Optional[str] = None
    min_investment_usd_approx: Optional[float] = None
    off_plan_eligible: Optional[int] = 1
    foreign_freehold_allowed: Optional[int] = 1
    key_tax_benefits: Optional[str] = None
    special_notes: Optional[str] = None
    source_url: str = Field(..., description="Official residency / developer reference URL")
    scraped_at: str = Field(default_factory=get_utc_now)


# ------------------------------------------------------------------------------
# 2. DARGLOBAL PROJECTS
# ------------------------------------------------------------------------------
class DarGlobalProjectItem(BaseModel):
    project_id: str = Field(..., description="Canonical ID: dg_<slug>")
    source: str = Field("darglobal", description="Data source origin")
    project_name: str = Field(..., description="Official project title")
    parent_masterplan: Optional[str] = None
    brand_partner: Optional[str] = None
    asset_vertical: str = Field("residential", description="residential, hospitality, golf, commercial, mixed_use")
    property_types: List[str] = Field(default_factory=list)
    status: str = Field("under_development", description="under_development, launching_soon, ready, sold_out, unknown")
    country: str = Field("UAE", description="FK -> residency_and_market_rules.country")
    city: str = Field(..., description="Primary city location")
    district_or_location: Optional[str] = None
    unit_configurations: List[str] = Field(default_factory=list)
    area_sqm_min: Optional[float] = None
    area_sqm_max: Optional[float] = None
    starting_price_amount: Optional[float] = None
    starting_price_currency: Optional[str] = "USD"
    starting_price_usd_est: Optional[float] = None
    completion_date: Optional[str] = None
    freehold_for_foreigners: Optional[int] = 1
    residency_eligible: Optional[int] = 1
    residency_min_investment: Optional[str] = None
    tokenization_available: Optional[int] = 0
    amenities: List[str] = Field(default_factory=list)
    brochure_url: Optional[str] = None
    project_url: str = Field(..., description="Canonical project detail URL")
    short_summary: Optional[str] = None
    scraped_at: str = Field(default_factory=get_utc_now)


# ------------------------------------------------------------------------------
# 3. WASALT PROJECTS
# ------------------------------------------------------------------------------
class WasaltProjectItem(BaseModel):
    project_id: str = Field(..., description="Canonical ID: ws_proj_<id_or_slug>")
    source: str = Field("wasalt_projects", description="Data source origin")
    project_name: str = Field(..., description="Official project name")
    developer_name: Optional[str] = None
    country: str = Field("Saudi Arabia", description="FK -> residency_and_market_rules.country")
    city: str = Field(..., description="City location")
    district: Optional[str] = None
    project_type: str = Field("residential_compound", description="residential_compound, masterplan_plots, mixed_use, villas, apartments, other")
    starting_price_sar: Optional[float] = None
    area_range_sqm: Optional[str] = None
    available_units_count: Optional[int] = None
    wafi_registered: Optional[int] = None
    completion_status: Optional[str] = None
    amenities: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    project_url: str = Field(..., description="Canonical project URL")
    scraped_at: str = Field(default_factory=get_utc_now)


# ------------------------------------------------------------------------------
# 4. WASALT LISTINGS
# ------------------------------------------------------------------------------
class WasaltListingItem(BaseModel):
    listing_id: str = Field(..., description="Canonical ID: wasalt_<numeric_id>")
    ref_no: Optional[str] = None
    source: str = Field("wasalt", description="Data source origin")
    wasalt_project_id: Optional[str] = None
    listing_purpose: str = Field("buy", description="buy, rent, daily_rental, barter, international")
    property_usage: str = Field("residential", description="residential, commercial, mixed_use, unknown")
    property_type: str = Field(..., description="apartment, villa, duplex, land, floor, building, rest_house, chalet, studio, office, shop, warehouse, compound, other")
    title: str = Field(..., description="Clean English descriptive title")
    country: str = Field("Saudi Arabia", description="FK -> residency_and_market_rules.country")
    city: str = Field(..., description="Standardized English city name")
    region_zone: Optional[str] = None
    district: Optional[str] = None
    street_name: Optional[str] = None
    plan_number: Optional[str] = None
    price_amount: Optional[float] = None
    price_currency: str = Field("SAR")
    price_period: str = Field("total", description="total, yearly, monthly, daily, negotiable")
    monthly_rent_display: Optional[float] = None
    built_up_area_sqm: Optional[float] = None
    land_area_sqm: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    living_rooms: Optional[int] = None
    guest_rooms: Optional[int] = None
    furnishing_status: str = Field("unknown", description="furnished, semi_furnished, unfurnished, unknown")
    property_age_years: Optional[str] = None
    building_year: Optional[int] = None
    facade_direction: Optional[str] = None
    street_width_m: Optional[float] = None
    utilities: List[str] = Field(default_factory=list)
    other_obligations: Optional[str] = None
    broker_agency_name: Optional[str] = None
    broker_agent_name: Optional[str] = None
    fal_license_no: Optional[str] = None
    rega_ad_license_no: Optional[str] = None
    rega_status: str = Field("unknown", description="active, expired, unknown")
    rega_issue_date: Optional[str] = None
    rega_expiry_date: Optional[str] = None
    ad_source: Optional[str] = None
    views_last_7_days: Optional[int] = None
    description: Optional[str] = None
    listing_url: str = Field(..., description="Canonical listing URL")
    last_updated_text: Optional[str] = None
    scraped_at: str = Field(default_factory=get_utc_now)


# ------------------------------------------------------------------------------
# 5. WASALT AUCTION GROUPS
# ------------------------------------------------------------------------------
class WasaltAuctionGroupItem(BaseModel):
    auction_group_id: str = Field(..., description="Numeric group ID e.g. 1167")
    auction_title: str = Field(..., description="Official auction title")
    auction_category: str = Field("real_estate", description="real_estate, vehicles, movable_assets, mixed")
    auction_type: str = Field("infath_judicial", description="infath_judicial, private, hybrid, unknown")
    city: Optional[str] = None
    status: str = Field("live", description="live, upcoming_registration_open, ended, cancelled")
    total_assets: int = Field(0)
    min_deposit_sar: Optional[float] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    sales_agent_name: Optional[str] = None
    sales_agent_fal_license: Optional[str] = None
    is_test_record: int = Field(0, description="1 if title/firm matches test/sandbox pattern")
    group_description: Optional[str] = None
    auction_url: str = Field(..., description="Canonical auction group URL")
    scraped_at: str = Field(default_factory=get_utc_now)


# ------------------------------------------------------------------------------
# 6. WASALT AUCTION ASSETS
# ------------------------------------------------------------------------------
class WasaltAuctionAssetItem(BaseModel):
    asset_id: str = Field(..., description="<auction_group_id>_<asset_number>")
    auction_group_id: str = Field(..., description="FK -> wasalt_auction_groups.auction_group_id")
    asset_number: int = Field(..., description="Lot/item sequence number")
    asset_title: str = Field(..., description="Asset title")
    asset_category: str = Field("real_estate", description="real_estate, vehicle, movable_asset")
    city_or_location: Optional[str] = None
    asset_status: str = Field("live", description="live, upcoming, sold, closed, cancelled")
    deposit_amount_sar: Optional[float] = None
    starting_bid_sar: Optional[float] = None
    highest_bid_sar: Optional[float] = None
    total_bidders: Optional[int] = None
    bids_count: Optional[int] = None
    followers_count: Optional[int] = None
    area_sqm: Optional[float] = None
    asset_specs: Dict[str, Any] = Field(default_factory=dict)
    asset_url: str = Field(..., description="Asset detail URL")
    scraped_at: str = Field(default_factory=get_utc_now)


# ------------------------------------------------------------------------------
# 7. DOCUMENT REGISTRY (Bridge Table)
# ------------------------------------------------------------------------------
class DocumentRegistryItem(BaseModel):
    doc_id: str = Field(..., description="Matches YAML doc_id in .md file")
    source_platform: str = Field(..., description="'darglobal' or 'wasalt'")
    doc_category: str = Field(..., description="corporate, project_narrative, investment_guide, faq, legal, blog, press, insights, service_guide")
    title: str = Field(..., description="Human-readable title")
    file_path: str = Field(..., description="Relative path from workspace root")
    linked_table: Optional[str] = None
    linked_entity_id: Optional[str] = None
    country: str = Field("Multi")
    city: str = Field("Multi")
    word_count: int = Field(0)
    source_urls: List[str] = Field(default_factory=list)
    last_scraped: str = Field(default_factory=get_utc_now)


# ------------------------------------------------------------------------------
# MARKDOWN YAML FRONTMATTER
# ------------------------------------------------------------------------------
class MarkdownFrontmatter(BaseModel):
    doc_id: str
    source_platform: str
    doc_category: str
    title: str
    country: str = "Multi"
    city: str = "Multi"
    linked_table: Optional[str] = None
    linked_entity_id: Optional[str] = None
    source_urls: List[str]
    last_scraped: str
