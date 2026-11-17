"""Gera respostas falsas no formato da SerpApi para testar sem gastar cota."""
import json, sys
def resp(preco, level="typical", cia="Emirates"):
    return {"best_flights":[{"price":preco,"total_duration":1980,
             "flights":[{"airline":cia},{"airline":cia}]}],
            "other_flights":[{"price":preco+3000,"flights":[{"airline":"Qatar Airways"}]}],
            "price_insights":{"lowest_price":preco,"price_level":level,"typical_price_range":[38000,52000]}}
m = {"_default": resp(46000)}
# promoção escondida: Pequim saindo 14/05 voltando 29/05 (só achável no refino de vizinhos)
m["PEK,PKX|2027-05-13|2027-05-29"] = resp(36500, "low", "Air China")
# Xangai um pouco mais barato numa data da grade
m["PVG,SHA|2027-05-10|2027-05-26"] = resp(42000, "typical", "Turkish Airlines")
json.dump(m, open(sys.argv[1],"w"), indent=1)
