#!/usr/bin/env python3
"""
Simple test file for pyslick local LLM patching.
This file has a bug that needs to be fixed.
"""

def calculate_discount(price, discount_percent):
    """Calculate discounted price."""
    return price * discount_percent

def format_currency(amount):
    """Format amount as currency string."""
    return f"${amount:.2f}"

def main():
    original_price = 100.0
    discount = 0.2  # 20% discount
    
    discounted_price = calculate_discount(original_price, discount)
    print(f"Original price: {format_currency(original_price)}")
    print(f"Discounted price: {format_currency(discounted_price)}")

if __name__ == "__main__":
    main()
