# Flour mill capacities: source and licence status

**Source:** Sosland Publishing, *Grain & Milling Annual 2027*, "Milling: Multiple Companies, US and Canada" (printed pages 106-118: each company's mills by location with daily capacity in cwt) and "Wheat Flour Mills" (126-147). Read at https://www.nxtbook.com/sosland/gma/gma_2027_11_01/index.php#/p/106 (page images only, no text layer).

**Terms (page 4 of the book, verbatim):** "©Sosland Publishing Company. All rights reserved. Reproduction of the whole or any part of the contents without written permission is prohibited." The same notice says the book costs $100 a copy for non-subscribers to *Milling & Baking News*.

**Status (2026-11-09):** Kolten decided to go ahead ("Lets go with option 2, we subscribe to global grain": JSA subscribes to the Sosland / Global Grain family of publications) after being shown the sentence above. The US wheat flour mills (printed pages 126-136) were transcribed by hand from the page images into `private/flour_mills_gma2027.csv` (git-ignored): 139 US mills plus one in Puerto Rico, 1,532,653 cwt a day, every state's mill count and capacity checked against the totals the book prints. Two mills list no capacity. Coordinates are Census gazetteer place points (five unincorporated places placed by hand). No permission request has been sent to Sosland.

**How it is kept private:** this repository is public, so the list is not committed in the clear. `build_flour_mills_blob.py` encrypts it to `data/wheat/flour_mills.enc` (Fernet) and `flour_mills.py` decrypts it with the `MILLS_KEY` secret (Streamlit Cloud Secrets, or `.streamlit/secrets.toml` locally). Without the key the model keeps its regional capacity stand-in. Only capacity, storage, rail and the classes each mill lists are kept; contact names, emails and phone numbers are not.

**Not done:** the "Multiple Companies" pages (106-118) repeat these mills by company and were not transcribed; Canada, the Caribbean and Mexico (136-147) were not transcribed; the class split of each mill's grind is open (the classes each mill lists are in the file).
