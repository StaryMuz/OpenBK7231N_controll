Funkční projekt v online provozu.
Projekt je určen pro práci v České republice a rozlišuje letní a zimní čas.
Ve večerních hodinách si z OTE stáhne data o cenách elektřiny pro následující den.
Při rozednívání začne kontrolovat cenu elektřiny tak, že každou čtvrthodinu porovná cenu s nastaveným limitem a pokud je cena nižší než limit, sepne Wifi relé v domácnosti.
Wifi relé spíná stykač, který připojí tepelnou zátěž k odběru. Stykači jsou předřazena řízená SSR relé a spotřeba zátěží nepřekročí možné přetoky z výroby FVE.
Informaci o záporných cenách a informaci o přepnutí stavu relé zasílá projekt na můj Telegram pro přehled o funkčnosti.
Zajistí se tak nulová dodávka z FVE do distribuční sítě v době, kdy by cena za dodávku do sítě znamenala finanční ztrátu.
Je využit broker pro spojení relé s tímto projektem. Relé je použito z číny, EARU EAWCBT-J a je do něj nahrán OpenBK7231N pro využití s běžným brokerem.
Poslední aktualizace projektu 8.6.2026 (snížena průběžná zátěž CPU serveru běžícím skryptem a zajištěn chod v přesných časech bez časových skluzů cron).

Tagy:
fotovoltaická, spot, denní trhy, smart, chytrá, přetoky

A fully functional project currently in operation. 
The project is designed for use in the Czech Republic and distinguishes between summer and winter time. 
In the evening, it downloads data on electricity prices for the following day from OTE. At dawn, it begins monitoring the electricity price by comparing it to a set limit every 15 minutes; if the price is lower than the limit, it activates the Wi-Fi relay in the home. 
The Wi-Fi relay activates a contactor, which connects the heating load to the power supply. The contactors are preceded by controlled SSR relays, and the load consumption does not exceed the possible excess feed-in from the PV system. 
The project sends information about negative prices and relay status changes to my Telegram channel for an overview of its functionality. 
This ensures zero power supply from the PV system to the distribution grid at times when the price for feeding power into the grid would result in a financial loss. 
A broker is used to connect the relay to this project. The relay is a Chinese model, EARU EAWCBT-J, and has OpenBK7231N loaded onto it for use with a standard broker. 
Last project update: June 8, 2026 (reduced ongoing CPU load on the server caused by the running script and ensured operation at precise times without cron time slippage).

Tags: photovoltaic, spot, daily markets, smart, overflow
