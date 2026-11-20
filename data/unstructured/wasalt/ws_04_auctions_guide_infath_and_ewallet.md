---
doc_id: ws_04_auctions
source_platform: wasalt
doc_category: service_guide
title: 'Wasalt Infath & Private Auctions: Bidding Mechanics, E-Wallet Deposit, and
  Lot Settlement'
country: Saudi Arabia
city: Multi
linked_table: wasalt_auction_groups
linked_entity_id: null
source_urls:
- https://auction.wasalt.sa/en
- https://auction.wasalt.sa/en/terms-and-conditions
last_scraped: '2026-11-18T18:53:11.278021+00:00'
---

# Wasalt Infath & Private Auctions Guide

## Overview of Judicial & Private Auctions in Saudi Arabia
Wasalt operates Saudi Arabia's premier digital auction platform in full partnership with **Infath** (The Entrustment and Liquidation Center) as well as private liquidators and licensed brokerage firms. Under Saudi judicial decree and REGA (Real Estate General Authority) regulatory frameworks, the platform conducts transparent, competitive bidding for three primary asset classes:
1. **Real Estate Assets**: Prime commercial plots, residential land, multi-family apartment buildings, commercial showrooms, industrial warehouses, and luxury villas.
2. **Vehicles**: Fleet liquidations, commercial trucks, passenger sedans, luxury SUVs, and heavy construction equipment.
3. **Movable Assets & Equipment**: Industrial machinery, plant assets, office equipment, and inventories.

## How Bidding Works on Wasalt
1. **Account Verification**: Bidders must register an account and complete National ID / Iqama verification via Absher (Nafath) and ensure valid AML/KYC standing.
2. **E-Wallet Deposit Allocation**:
   - Each auction group establishes a mandatory minimum deposit (`min_deposit_sar`), typically between SAR 5,000 and SAR 250,000 depending on lot valuation.
   - Bidders fund their digital Wasalt Auction E-Wallet via SADAD, wire transfer, or Mada/Visa.
   - Once allocated to an auction group, the deposit unlocks bidding eligibility across the constituent lots.
   - Unsuccessful bidders receive an instantaneous, automatic release of their deposit back to their available wallet balance upon auction close.
3. **Live Bidding Cycles & Increments**:
   - Auctions feature scheduled opening and closing timestamps.
   - Bids must meet or exceed defined bid slab increments (e.g. SAR 1,000 increments below SAR 500,000; SAR 5,000 increments above).
   - Dynamic Extension Rule (Anti-Sniping): Any bid submitted within the final two minutes automatically extends the timer by an additional two minutes, guaranteeing fair market price discovery.
4. **Winning Lot Settlement & Legal Transfer**:
   - Winning bidders are issued an official Award Certificate.
   - Full settlement of the remaining balance, applicable VAT (typically 15%), and buyer commission (typically 2.5%) must be completed within the statutorily specified period (normally 5 to 10 business days).
   - Real estate ownership title deed conveyance is executed electronically through the Saudi Ministry of Justice Najiz / Real Estate Registry portal.
