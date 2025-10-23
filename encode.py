from urllib.parse import quote_plus

# --- PASTE YOUR PLAIN-TEXT PASSWORD HERE ---
my_password = "ygap/%KyiR9Zpr+"

encoded_password = quote_plus(my_password)

print("\n--- Copy the line below and paste it into your .env file ---\n")
print(encoded_password)