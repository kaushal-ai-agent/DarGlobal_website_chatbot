"""Configuration, constants, paths, and partition grids for DarGlobal & Wasalt scraper."""

import os
import sys
from pathlib import Path
from typing import Dict, List, Any

# Root paths
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"

# Structured Data Sinks
STRUCTURED_DIR = DATA_DIR / "structured"
RAW_JSONL_DIR = STRUCTURED_DIR / "raw_jsonl"
DB_DIR = STRUCTURED_DIR / "db"
MANIFESTS_DIR = STRUCTURED_DIR / "manifests"
DB_PATH = DB_DIR / "real_estate.db"

# Unstructured Knowledge Base Sinks
UNSTRUCTURED_DIR = DATA_DIR / "unstructured"
DG_UNSTRUCTURED_DIR = UNSTRUCTURED_DIR / "darglobal"
WS_UNSTRUCTURED_DIR = UNSTRUCTURED_DIR / "wasalt"

# Checkpoints and Dead-Letter Queue
CHECKPOINT_DIR = DATA_DIR / "checkpoints"
STATE_FILE = CHECKPOINT_DIR / "scraper_state.json"
FAILED_URLS_FILE = CHECKPOINT_DIR / "failed_urls.jsonl"
COMPLETENESS_REPORT_FILE = CHECKPOINT_DIR / "completeness_report.json"

# Target Hostnames
DARGLOBAL_BASE_URL = "https://darglobal.co.uk"
WASALT_BASE_URL = "https://wasalt.sa/en"
AUCTION_BASE_URL = "https://auction.wasalt.sa/en"
BLOG_BASE_URL = "https://blog.wasalt.sa/en"
BLOG_WP_API_URL = "https://blog.wasalt.sa/en/wp-json/wp/v2/posts"
BUSINESS_BASE_URL = "https://business.wasalt.sa/en"

# Browser Simulation Headers
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

# Standardized Country Normalizer
COUNTRY_NORMALIZER: Dict[str, str] = {
    "sa": "Saudi Arabia",
    "ksa": "Saudi Arabia",
    "saudi": "Saudi Arabia",
    "saudi arabia": "Saudi Arabia",
    "kingdom of saudi arabia": "Saudi Arabia",
    "uae": "UAE",
    "united arab emirates": "UAE",
    "dubai": "UAE",
    "ras al khaimah": "UAE",
    "rak": "UAE",
    "oman": "Oman",
    "muscat": "Oman",
    "qatar": "Qatar",
    "doha": "Qatar",
    "spain": "Spain",
    "marbella": "Spain",
    "costa del sol": "Spain",
    "uk": "UK",
    "united kingdom": "UK",
    "london": "UK",
    "maldives": "Maldives",
    "greece": "Greece",
    "athens": "Greece",
    "usa": "USA",
}

# Seed data for residency_and_market_rules
RESIDENCY_SEEDS = [
    {
        "country": "Saudi Arabia",
        "residency_program_name": "Premium Residency (Real Estate Owner Track)",
        "min_investment_local": "4,000,000 SAR",
        "min_investment_usd_approx": 1066667.0,
        "off_plan_eligible": 1,
        "foreign_freehold_allowed": 1,
        "key_tax_benefits": "0% personal income tax, 0% property tax, 30-year regional headquarters corporate tax exemption, visit-visa local bank account access, SAR-USD currency peg (3.75).",
        "special_notes": "Requires property ownership valued at 4M SAR or above, unmortgaged, and clear deed. REGA-regulated.",
        "source_url": "https://darglobal.co.uk/why-invest",
    },
    {
        "country": "UAE",
        "residency_program_name": "UAE 10-Year Golden Visa (Property Investor)",
        "min_investment_local": "2,000,000 AED",
        "min_investment_usd_approx": 544500.0,
        "off_plan_eligible": 1,
        "foreign_freehold_allowed": 1,
        "key_tax_benefits": "0% personal income tax, 0% capital gains tax, 0% inheritance tax, 0% wealth tax.",
        "special_notes": "Off-plan developments eligible with approved developer payment plans or bank mortgage. Covers investor, spouse, and dependents.",
        "source_url": "https://darglobal.co.uk/why-invest",
    },
    {
        "country": "Oman",
        "residency_program_name": "Investor Residency Programme (First-Class / Second-Class)",
        "min_investment_local": "250,000 OMR",
        "min_investment_usd_approx": 650000.0,
        "off_plan_eligible": 1,
        "foreign_freehold_allowed": 1,
        "key_tax_benefits": "0% personal income tax, 0% capital gains tax on residential property, full foreign ownership in Integrated Tourism Complexes (ITCs).",
        "special_notes": "Sanad Mabdayi pre-completion temporary title deed enables residency rights during construction phase at projects like AIDA.",
        "source_url": "https://darglobal.co.uk/why-invest",
    },
    {
        "country": "Qatar",
        "residency_program_name": "Real Estate Permanent Residency / Investor Visa",
        "min_investment_local": "3,650,000 QAR (Permanent) / 730,000 QAR (5-Year)",
        "min_investment_usd_approx": 1000000.0,
        "off_plan_eligible": 1,
        "foreign_freehold_allowed": 1,
        "key_tax_benefits": "0% property tax, 0% personal income tax, free healthcare and education benefits for permanent residents.",
        "special_notes": "Projects in Lusail, Simaisma, and The Pearl offer designated freehold zones for international investors.",
        "source_url": "https://darglobal.co.uk/why-invest",
    },
    {
        "country": "Spain",
        "residency_program_name": "Spain Golden Visa / Real Estate Residence",
        "min_investment_local": "500,000 EUR",
        "min_investment_usd_approx": 545000.0,
        "off_plan_eligible": 1,
        "foreign_freehold_allowed": 1,
        "key_tax_benefits": "Schengen area freedom of movement, Beckham Law favorable expat tax regime option.",
        "special_notes": "Applies across luxury developments in Marbella, Benahavís, and Madrid.",
        "source_url": "https://darglobal.co.uk/why-invest",
    },
    {
        "country": "UK",
        "residency_program_name": "UK Innovator / Global Talent & High-Net-Worth Freehold Ownership",
        "min_investment_local": "Freehold Market Discretionary",
        "min_investment_usd_approx": 2500000.0,
        "off_plan_eligible": 1,
        "foreign_freehold_allowed": 1,
        "key_tax_benefits": "Strong British legal protections, high rental yield potential in Central London, transparent land registry.",
        "special_notes": "Prime London properties (e.g. 7&8 Albert Hall Mansions) offer long leasehold/freehold investment structures.",
        "source_url": "https://darglobal.co.uk/why-invest",
    },
    {
        "country": "Maldives",
        "residency_program_name": "Corporate Residency & Luxury Hospitality Leasehold",
        "min_investment_local": "1,000,000 USD",
        "min_investment_usd_approx": 1000000.0,
        "off_plan_eligible": 1,
        "foreign_freehold_allowed": 0,
        "key_tax_benefits": "High luxury resort hospitality yields, 0% personal income tax on offshore earnings.",
        "special_notes": "50 to 99-year long-term strata-title leases backed by luxury hotel operator guarantees.",
        "source_url": "https://darglobal.co.uk/why-invest",
    },
    {
        "country": "Greece",
        "residency_program_name": "Greece Golden Visa Programme",
        "min_investment_local": "800,000 EUR (Tier 1) / 400,000 EUR (Tier 2)",
        "min_investment_usd_approx": 870000.0,
        "off_plan_eligible": 1,
        "foreign_freehold_allowed": 1,
        "key_tax_benefits": "Full Schengen travel access, low property transfer tax rates, non-dom tax incentives.",
        "special_notes": "Direct pathway to European permanent residency and citizenship.",
        "source_url": "https://darglobal.co.uk/why-invest",
    },
    {
        "country": "USA",
        "residency_program_name": "EB-5 Immigrant Investor Program / Real Estate Freehold",
        "min_investment_local": "800,000 USD (TEA) / 1,050,000 USD",
        "min_investment_usd_approx": 800000.0,
        "off_plan_eligible": 1,
        "foreign_freehold_allowed": 1,
        "key_tax_benefits": "Full US residency pathway, transparent property rights, strong capital appreciation in primary luxury metropolitan markets.",
        "special_notes": "Targeted Employment Area (TEA) eligible luxury mixed-use or direct freehold private residential acquisitions.",
        "source_url": "https://darglobal.co.uk/why-invest",
    },
]

# Partitioning Grid for Wasalt listings to bypass search pagination cap
PARTITION_PURPOSES = ["buy", "rent", "daily_rental", "barter", "international"]
PARTITION_CITIES = ["Riyadh", "Jeddah", "Makkah", "Madinah", "Dammam", "Khobar"]
PARTITION_PROPERTY_TYPES = ["villa", "apartment", "land", "duplex", "floor", "building", "commercial"]

# Concurrency throttling
SEMAPHORE_LIMIT = 5
BATCH_SIZE = 10
