import numpy as np
import pandas as pd
import sys
from datetime import datetime, timedelta, time
import talib as ta
from talib import MA_Type
from ib_insync import *
import schedule
import nest_asyncio

nest_asyncio.apply()  # enable nest asyncio
sys.setrecursionlimit(10 ** 9)  # set recursion limit to 1000000000
pd.options.mode.chained_assignment = None  # remove a warning


class get_data:
    """ A class to get ES Technical analysis and next 2 days expiration date and delta 60 option strikes for
    whatever ES price at """

    def __init__(self):
        pass

    @staticmethod
    def next_exp_weekday():
        """ Set next expiration date for contract 0 = Monday, 1 = Tuesday, etc..."""
        weekdays = {2: [5, 6, 0], 4: [0, 1, 2], 0: [3, 4]}
        today = datetime.today().weekday()
        for exp, day in weekdays.items():
            if today in day:
                return exp  # return the 2nd next weekday number

    @staticmethod
    def next_weekday(d, weekday):
        """ Translate weekdays number to a date for example next Mon = October 19th 2020"""
        days_ahead = weekday - d.weekday()
        if days_ahead <= 0:  # Target day already happened this week
            days_ahead += 7
        date_to_return = d + timedelta(days_ahead)  # 0 = Monday, 1=Tus self.ES day, 2=Wed self.ES day...
        return date_to_return.strftime('%Y%m%d')  # return the date in the form of (yearmonthday) ex:(20201019)

    def get_strikes_and_expiration(self):
        """ When used, returns strikes and expiration for the ES futures options"""
        ES = Future(symbol='ES', lastTradeDateOrContractMonth='20201218', exchange='GLOBEX',
                    currency='USD')
        ib.qualifyContracts(ES)
        expiration = self.next_weekday(datetime.today(), self.next_exp_weekday())
        chains = ib.reqSecDefOptParams(underlyingSymbol='ES', futFopExchange='GLOBEX', underlyingSecType='FUT',
                                       underlyingConId=ES.conId)
        chain = util.df(chains)
        strikes = chain[chain['expirations'].astype(str).str.contains(expiration)].loc[:, 'strikes'].values[0]
        [ESValue] = ib.reqTickers(ES)
        ES_price = ESValue.marketPrice()
        strikes = [strike for strike in strikes
                   if strike % 5 == 0
                   and ES_price - 10 < strike < ES_price + 10]
        return strikes, expiration

    def get_contract(self, right, net_liquidation):
        """ Get contracts for ES futures options by using get_strikes_and_expiration function"""
        strikes, expiration = self.get_strikes_and_expiration()
        for strike in strikes:
            contract = FuturesOption(symbol='ES', lastTradeDateOrContractMonth=expiration,
                                     strike=strike, right=right, exchange='GLOBEX')
            ib.qualifyContracts(contract)
            price = ib.reqMktData(contract, "", False, False)
            if float(price.last) * 50 >= net_liquidation:
                continue
            else:
                return contract

    def ES(self, ES):
        """ By have the dataframe of ES futures, this function will analyze and
        provide technicals using TA-lib library"""

        ES_df = util.df(ES)
        ES_df.set_index('date', inplace=True)
        ES_df.index = pd.to_datetime(ES_df.index)
        # ES_df['hours'] = ES_df.index.strftime('%H').astype(int)
        # ES_df['minutes'] = ES_df.index.strftime('%M').astype(int)
        # ES_df['hours + minutes'] = ES_df['hours'] * 100 + ES_df['minutes']
        # ES_df['Day_of_week'] = ES_df.index.dayofweek
        ES_df['RSI'] = ta.RSI(ES_df['close'])
        # ES_df['macd'], ES_df['macdsignal'], ES_df['macdhist'] = ta.MACD(ES_df['close'], fastperiod=12, slowperiod=26,
        #                                                                signalperiod=9)
        # ES_df['macd - macdsignal'] = ES_df['macd'] - ES_df['macdsignal']
        # ES_df['MA_9'] = ta.MA(ES_df['close'], timeperiod=9)
        # ES_df['MA_21'] = ta.MA(ES_df['close'], timeperiod=21)
        # ES_df['MA_200'] = ta.MA(ES_df['close'], timeperiod=200)
        ES_df['EMA_9'] = ta.EMA(ES_df['close'], timeperiod=9)
        ES_df['EMA_26'] = ta.EMA(ES_df['close'], timeperiod=26)
        ES_df['derv_1'] = np.gradient(ES_df['EMA_9'])
        # ES_df['EMA_9_26']=df['EMA_9']/df['EMA_26']
        # ES_df['EMA_50'] = ta.EMA(ES_df['close'], timeperiod=50)
        # ES_df['EMA_200'] = ta.EMA(ES_df['close'], timeperiod=200)
        ES_df['ATR'] = ta.ATR(ES_df['high'], ES_df['low'], ES_df['close'], timeperiod=20)
        ES_df['roll_max_cp'] = ES_df['high'].rolling(20).max()
        ES_df['roll_min_cp'] = ES_df['low'].rolling(20).min()
        ES_df['roll_max_vol'] = ES_df['volume'].rolling(25).max()
        # ES_df['vol/max_vol'] = ES_df['volume'] / ES_df['roll_max_vol']
        # ES_df['EMA_21-EMA_9'] = ES_df['EMA_21'] - ES_df['EMA_9']
        # ES_df['EMA_200-EMA_50'] = ES_df['EMA_200'] - ES_df['EMA_50']
        # ES_df['B_upper'], ES_df['B_middle'], ES_df['B_lower'] = ta.BBANDS(ES_df['close'], matype=MA_Type.T3)
        ES_df.dropna(inplace=True)
        return ES_df.iloc[-400:, :]


# self = Trade
class Trade:
    """ This class will trade the data from get_data class in interactive brokers. It includes strategy,
    buying/selling criteria, and controls all connections to interactive brokers orders.
    """

    def __init__(self):

        self.skip = False
        self.connect()
        ES = Future(symbol='ES', lastTradeDateOrContractMonth='20201218', exchange='GLOBEX', currency='USD')  # define
        # ES-Mini futures contract
        ib.qualifyContracts(ES)
        self.ES = ib.reqHistoricalData(contract=ES, endDateTime='', durationStr='2 D',
                                       barSizeSetting='1 min', whatToShow='TRADES', useRTH=False, keepUpToDate=True,
                                       timeout=10)  # start data collection for ES-Mini
        self.data_raw = res.ES(self.ES)
        self.stock_owned = np.zeros(2)  # get data from get data class
        self.option_position()  # check holding positions and initiate contracts for calls and puts
        ib.sleep(1)
        self.call_option_volume = np.ones(20)  # start call options volume array to get the max volume in the last 20
        self.put_option_volume = np.ones(
            20)  # start put options volume array to get the max volume in the last 20 ticks
        self.block_buying = 0  # Buying flag
        self.submitted = 0  # order submission flag
        self.portfolio = ib.portfolio()

        self.put_contract_price = 0.25 * round(
            ((self.put_option_price.ask + self.put_option_price.bid) / 2) / 0.25)  # calculate average put price
        self.call_contract_price = 0.25 * round(
            ((self.call_option_price.ask + self.call_option_price.bid) / 2) / 0.25)  # calculate average call price
        self.options_price = np.array(
            [self.call_contract_price, self.put_contract_price])  # set an array for options prices

        self.max_call_price = self.call_option_price.bid  # define max call price (use to compare to current price)
        self.max_put_price = self.put_option_price.bid  # define max put price (use to compare to current price)
        self.prev_cash = 0
        self.account = ib.accountSummary()  # get initial account value
        self.portfolio_value = float(self.account[29].value)  # set variables values
        self.cash_in_hand = float(self.account[22].value)  # set variables values
        self.unrealizedPNL = float(self.account[32].value)
        self.realizedPNL = float(self.account[33].value)
        self.reqId = []
        self.update = -1  # set this variable to -1 to get the last data in the get_data df

        ib.reqGlobalCancel()  # Making sure all orders for buying selling are canceled before starting trading

    def trade(self, ES, hasNewBar=None):
        #
        self.ES = ES
        buy_index = []  # set initial buy index to None
        sell_index = []  # set initial sell index to None
        take_profit = []  # set initial take profit index to None
        self.call_option_volume = self.roll_contract(self.call_option_volume,
                                                     self.call_option_price.bidSize)  # update call options volume
        self.put_option_volume = self.roll_contract(self.put_option_volume,
                                                    self.put_option_price.bidSize)  # update put options volume
        open_orders = ib.reqAllOpenOrders()  # get current open orders

        self.data_raw = res.ES(self.ES)

        df = self.data_raw[
            ['high', 'low', 'volume', 'close', 'RSI', 'ATR', 'roll_max_cp', 'roll_min_cp',
             'roll_max_vol', 'EMA_9', 'EMA_26', 'derv_1']].tail()  # filter data
        if self.stock_owned.any() > 0 and not np.isnan(self.max_call_price) and not np.isnan(
                self.max_put_price):
            self.max_call_price = self.call_option_price.bid if self.call_option_price.bid > self.max_call_price else \
                self.max_call_price

            self.max_put_price = self.put_option_price.bid if self.put_option_price.bid > self.max_put_price else \
                self.max_put_price  # check if holding positions and how much the max price for current position
        else:
            self.max_call_price = self.call_option_price.bid
            self.max_put_price = self.put_option_price.bid

        if self.stock_owned[0] > 0:
            print(f'Call cost was = {self.call_cost}')
            print((self.call_option_price.bid - self.call_cost))
        elif self.stock_owned[1] > 0:
            print(f'Put cost was = {self.put_cost}')
            print((self.put_option_price.bid - self.put_cost))

        buy_index, sell_index, take_profit = self.strategy(df, open_orders)  # set initial buy index to None

        if sell_index:  # start selling to stop loss

            for i in sell_index:
                # self.stock_owned[i] = 0

                if len(self.portfolio) > 0 and len(open_orders) == 0 and self.submitted == 0:
                    self.submitted = 1
                    contract = self.call_contract if i == 0 else self.put_contract
                    ib.qualifyContracts(contract)
                    price = ib.reqMktData(contract, '', False, False, None)

                    self.flatten_position(contract, price)

        if take_profit:  # start selling to take profit
            for i in take_profit:

                print(self.stock_owned[i])
                print(len(self.portfolio))

                if len(self.portfolio) > 0 and len(open_orders) == 0 and self.submitted == 0:
                    self.submitted = 1
                    contract = self.call_contract if i == 0 else self.put_contract
                    ib.qualifyContracts(contract)
                    price = ib.reqMktData(contract, '', False, False, None)

                    self.take_profit(contract, price)

        if buy_index:  # start buying to start trade

            for i in buy_index:
                contract = self.call_contract if i == 0 else self.put_contract
                ib.qualifyContracts(contract)

                if self.cash_in_hand > (self.options_price[i] * 50) and self.cash_in_hand > self.portfolio_value \
                        and (self.stock_owned[0] < 1 or self.stock_owned[1] < 1) and len(self.portfolio) == 0 and len(
                    open_orders) == 0 and self.submitted == 0:
                    self.submitted = 1
                    price = self.call_option_price if i == 0 else self.put_option_price
                    quantity = int((self.cash_in_hand / (self.options_price[i] * 50)))
                    self.block_buying = 1
                    self.open_position(contract=contract, quantity=quantity, price=price)

    def strategy(self, df, open_orders):
        """
        Strategy to trade is:

            Opening positions if:
                - Buying ES Calls options when ES breaks the resistance in the last 20 ticks
                - Buying ES Puts options when ES breaks the support in the last 20 ticks

            Closing positions if:

                - ES trend reverses
                - ES's ATR is more than 1.25 in the opposite direction
                - Option price is less losing more than 1.25 * 0.5 * ATR from the cost price
                - Option price made 20% profits
                - Option volume reduce to 1 / 8 in the next tick
        """
        buy_index = []  # set initial buy index to None
        sell_index = []  # set initial sell index to None
        take_profit = []  # set initial take profit index to None
        i = -1  # use to get the last data in dataframe

        stop_loss = 1.75 + 0.25 * round((df["ATR"].iloc[i]) / 0.25)  # set stop loss variable according to ATR
        ATR_factor = 0.25 * round((df["ATR"].iloc[i]) / 0.25) * 1.5
        # print("%%%%",((df["close"].iloc[i] > df["close"].iloc[i - 1] + (ATR_factor * df["ATR"].iloc[i - 1])) or ((not np.isnan(self.put_option_price.bid)) and abs(
        #         self.put_cost - self.put_option_price.bid) >= abs(stop_loss))) and self.submitted == 0 and len(open_orders) ==0)
        print(
            f'cash in hand = {self.cash_in_hand}, portfolio value = {self.portfolio_value}, unrealized PNL ='
            f' {self.unrealizedPNL} realized PNL = {self.realizedPNL}, holding = {self.stock_owned[0]} '
            f'calls and {self.stock_owned[1]} puts and ES = {self.data_raw.iloc[-1, 3]} and [call,puts] values are = '
            f'{self.options_price} and stop loss ={stop_loss} and max call price = {self.max_call_price} compared to '
            f'{self.call_option_price.bid} and max put price = {self.max_put_price} compared to '
            f'{self.put_option_price.bid} and RSI = {df["RSI"].iloc[-1]} and df["ATR"] = {df["ATR"][-1]} '
            f'and ATR_factor = {ATR_factor} and self.submitted = {self.submitted} and call volume = '
            f'{self.call_option_volume[-1]} and put volume = {self.put_option_volume[-1]} and max call volume = '
            f'{np.max(self.call_option_volume)} and max put volume = {np.max(self.put_option_volume)}')
        print((self.stock_owned[1] > 0) and ((df["close"].iloc[i - 1] > 0.0 and df["close"].iloc[i] > 0.0 and (
                    df["close"].iloc[i] > df["close"].iloc[i - 1] + (ATR_factor * df["ATR"].iloc[i - 1]))) or (
                                                         (not np.isnan(self.put_option_price.bid)) and abs(
                                                     self.put_cost - self.put_option_price.bid) >= abs(
                                                     stop_loss))) and self.submitted == 0 and len(open_orders) == 0)

        if df["high"].iloc[i] >= df["roll_max_cp"].iloc[i - 1] and \
                df["volume"].iloc[i] > df["roll_max_vol"].iloc[i - 1] \
                and df["RSI"].iloc[i] < 80 and (not (is_time_between(time(13, 50), time(14, 00)) or
                                                     is_time_between(time(15, 00), time(15, 15)))) and \
                buy_index == [] and self.stock_owned[0] == 0 and self.stock_owned[1] == 0 and self.submitted == 0:
            # conditions to buy calls
            tickers_signal = "Buy call"
            buy_index.append(0)

        elif df["low"].iloc[i] <= df["roll_min_cp"].iloc[i - 1] and \
                df["volume"].iloc[i] > df["roll_max_vol"].iloc[i - 1] \
                and df["RSI"].iloc[i] > 20 and (not (is_time_between(time(13, 50), time(14, 00)) or
                                                     is_time_between(time(15, 00), time(15, 15)))) and \
                buy_index == [] and self.stock_owned[0] == 0 and self.stock_owned[1] == 0 and self.submitted == 0:
            # conditions to sell calls
            tickers_signal = "Buy put"
            buy_index.append(1)

        elif (self.stock_owned[0] > 0) and (df["derv_1"].iloc[i] < df["derv_1"].iloc[i - 1] and
                                            df["derv_1"].iloc[i] < df["derv_1"].iloc[i - 2] and
                                            df["derv_1"].iloc[i] < df["derv_1"].iloc[i - 3] and
                                            df["derv_1"].iloc[i] < df["derv_1"].iloc[i - 4]) and len(self.portfolio) > 0 \
                and len(open_orders) == 0 and self.submitted == 0:  # conditions to sell calls to take profits
            tickers_signal = "take calls profit"
            take_profit.append(0)

        elif (self.stock_owned[1] > 0) and (df["derv_1"].iloc[i] > df["derv_1"].iloc[i - 1] and
                                            df["derv_1"].iloc[i] > df["derv_1"].iloc[i - 2] and
                                            df["derv_1"].iloc[i] > df["derv_1"].iloc[i - 3] and
                                            df["derv_1"].iloc[i] > df["derv_1"].iloc[i - 4]) and len(self.portfolio) > 0 \
                and len(open_orders) == 0 and self.submitted == 0:  # conditions to sell calls to take profits
            tickers_signal = "take puts profit"
            take_profit.append(1)


        # elif (self.stock_owned[0] > 0) and (df["low"].iloc[i] <= df["roll_min_cp"].iloc[i - 1] and
        #                                     df["volume"].iloc[i] > df["roll_max_vol"].iloc[i - 1]) and \
        #         self.stock_owned[1] == 0 and self.block_buying == 0 and self.submitted == 0:
        #     # conditions to sell calls and buy puts if trend reversed
        #     tickers_signal = "sell call and buy puts"
        #     sell_index.append(0)
        #     buy_index.append(1)
        #
        # elif (self.stock_owned[1] > 0) and (df["high"].iloc[i] >= df["roll_max_cp"].iloc[i - 1] and
        #                                     df["volume"].iloc[i] > df["roll_max_vol"].iloc[i - 1]) \
        #         and self.stock_owned[0] == 0 and self.block_buying == 0 and self.submitted == 0:
        #     # conditions to buy calls and sell puts if trend reversed
        #     tickers_signal = "sell put and buy calls"
        #     sell_index.append(1)
        #     buy_index.append(0)
        #
        #
        # elif (self.stock_owned[0] > 0) and (
        #         (self.call_option_volume[-1] < self.call_option_volume[-4] and self.call_option_volume[-1] <
        #          self.call_option_volume[-2] < self.call_option_volume[-3]) or
        #         (self.max_call_price / self.call_cost) > 1.10) and (
        #         self.max_call_price - 0.25 > self.call_contract_price + 0.25) and len(self.portfolio) > 0 \
        #         and len(open_orders) == 0 and self.submitted == 0:  # conditions to sell calls to take profits
        #     tickers_signal = "take calls profit"
        #     take_profit.append(0)
        #
        # elif (self.stock_owned[1] > 0) and (
        #         (self.put_option_volume[-1] < self.put_option_volume[-4] and self.put_option_volume[-1] <
        #          self.put_option_volume[-2] < self.put_option_volume[-3]) or
        #         (self.max_put_price / self.put_cost) > 1.14) and (
        #         self.max_put_price - 0.25 > self.put_contract_price + 0.25) and len(self.portfolio) > 0 \
        #         and len(open_orders) == 0 and self.submitted == 0:
        #     # conditions to sell puts to take profits
        #     tickers_signal = "take puts profit"
        #     take_profit.append(1)

        # elif (self.stock_owned[0] > 0) and ((df["close"].iloc[i - 1] > 0.0 and df["close"].iloc[i] > 0.0 and (
        #         df["close"].iloc[i] < df["close"].iloc[i - 1] - (
        #         ATR_factor * df["ATR"].iloc[i - 1]))) or ((not np.isnan(self.call_option_price.bid)) and abs(
        #     self.call_cost - self.call_option_price.bid) >= abs(stop_loss))) \
        #         and self.submitted == 0 and len(open_orders) == 0:
        #     # conditions to sell calls to stop loss
        #     tickers_signal = "sell call"
        #     sell_index.append(0)
        #
        #
        # elif (self.stock_owned[1] > 0) and ((df["close"].iloc[i - 1] > 0.0 and df["close"].iloc[i] > 0.0 and (
        #         df["close"].iloc[i] > df["close"].iloc[i - 1] + (
        #         ATR_factor * df["ATR"].iloc[i - 1]))) or ((not np.isnan(self.put_option_price.bid)) and abs(
        #     self.put_cost - self.put_option_price.bid) >= abs(stop_loss))) \
        #         and self.submitted == 0 and len(open_orders) == 0:
        #     # conditions to sell puts to stop loss
        #     tickers_signal = "sell put"
        #     sell_index.append(1)

        else:
            tickers_signal = "Hold"
            sell_index = []
            buy_index = []
            take_profit = []

        print(f'stocks owning = {self.stock_owned}')
        print(tickers_signal)
        print(f'check stocks owning = {not self.unrealizedPNL == 0 and not self.stock_owned.any() > 0}')
        print('$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$')

        return buy_index, sell_index, take_profit

    def error(self, reqId=None, errorCode=None, errorString=None, contract=None):  # error handler
        print(errorCode, errorString)

        if errorCode in [2104, 2108, 2158, 10182, 1102, 2106, 2107] and len(self.reqId) < 1:
            self.reqId.append(reqId)
            ib.cancelHistoricalData(self.ES)
            del self.ES
            ib.sleep(50)
            ES = Future(symbol='ES', lastTradeDateOrContractMonth='20201218', exchange='GLOBEX',
                        currency='USD')  # define
            # ES-Mini futures contract
            ib.qualifyContracts(ES)
            self.ES = ib.reqHistoricalData(contract=ES, endDateTime='', durationStr='2 D',
                                           barSizeSetting='1 min', whatToShow='TRADES', useRTH=False, keepUpToDate=True,
                                           timeout=10)  # start data collection for ES-Mini
            print('attempt to restart data check')
            if len(self.ES) == 0:
                print(self.ES)
                self.error()
                self.reqId = []
            else:
                ib.sleep(1)
                self.reqId = []
                self.ES.updateEvent += self.trade
                self.trade(self.ES)

        elif errorCode == 201:
            self.option_position()

    def flatten_position(self, contract, price):  # flat position to stop loss
        self.submitted = 1
        print('flatttttttttttttttttttttttttttttttttttttttttttttttttttttt')
        portfolio = self.portfolio
        for each in portfolio:  # check current position and select contract
            print(price.bid)
            if is_time_between(time(15, 00), time(15, 15)) or each.contract.right != contract.right or price.bid < 0.25:
                return
            ib.qualifyContracts(each.contract)
            if each.position > 0:  # Number of active Long portfolio
                action = 'SELL'  # to offset the long portfolio
            elif each.position < 0:  # Number of active Short portfolio
                action = 'BUY'  # to offset the short portfolio
            else:
                assert False
            totalQuantity = abs(each.position)  # check holding quantity

            print(f'price = {price.bid}')
            print(f'Flatten Position: {action} {totalQuantity} {contract.localSymbol}')
            order = LimitOrder(action, totalQuantity, price.bid) if each.position > 0 \
                else MarketOrder(action, totalQuantity)  # closing position as fast as possible
            trade = ib.placeOrder(each.contract, order)
            ib.sleep(10)  # waiting 10 secs
            if not trade.orderStatus.remaining == 0:
                ib.cancelOrder(order)  # canceling order if not filled
                self.submitted = 0
            else:
                self.submitted = 0
                self.stock_owned = np.zeros(2)

            print(trade.orderStatus.status)

        return

    def take_profit(self, contract, price):  # start taking profit

        print('take_________________profit')
        portfolio = self.portfolio
        for each in portfolio:
            if (price.bid - 0.5) <= 0.25 + (each.averageCost / 50):  # check if profit did happen
                print(price.bid, each.averageCost / 50)
                print('cancel sell no profit yet')
                self.submitted = 0
                return
            ib.qualifyContracts(each.contract)
            if each.position > 0:  # Number of active Long portfolio
                action = 'SELL'  # to offset the long portfolio
            elif each.position < 0:  # Number of active Short portfolio
                action = 'BUY'  # to offset the short portfolio
            else:
                assert False
            totalQuantity = abs(each.position)

            print(f'price = {price.bid - 0.25}')
            print(f'Take profit Position: {action} {totalQuantity} {contract.localSymbol}')

            order = LimitOrder(action, totalQuantity, price.bid)
            trade = ib.placeOrder(each.contract, order)
            ib.sleep(15)
            if not trade.orderStatus.remaining == 0:
                ib.cancelOrder(order)
                self.submitted = 0
            else:
                self.stock_owned = np.zeros(2)

                self.submitted = 0
            print(trade.orderStatus.status)

        return

    def open_position(self, contract, quantity, price):  # start position
        order = LimitOrder('BUY', quantity,
                           price.ask)  # round(25 * round(price[i]/25, 2), 2))
        trade = ib.placeOrder(contract, order)
        print(f'buying {"CALL" if contract.right == "C" else "PUT"}')
        ib.sleep(15)
        if not trade.orderStatus.status == "Filled":
            ib.cancelOrder(order)
            self.submitted = 0
        else:

            self.submitted = 0

        self.submitted = 0
        print(trade.orderStatus.status)
        self.block_buying = 0

        return

    # def positions(self,contract):
    #     self.stock_owned = np.zeros(2)
    #     self.portfolio = ib.portfolio()

    def option_position(self, event=None):
        self.stock_owned = np.zeros(2)
        position = ib.portfolio()
        self.portfolio = position
        call_position = None
        put_position = None

        for each in position:
            if each.contract.right == 'C':
                call_position = each.contract
                ib.qualifyContracts(call_position)
                self.stock_owned[0] = each.position
                self.call_cost = 0.25 * round(each.averageCost / 50 / 0.25)
            elif each.contract.right == 'P':
                put_position = each.contract
                ib.qualifyContracts(put_position)
                self.stock_owned[1] = each.position
                self.put_cost = 0.25 * round(each.averageCost / 50 / 0.25)
            else:
                self.call_cost = -1
                self.put_cost = -1

        self.call_contract = call_position if not pd.isna(call_position) else res.get_contract('C', 2000)
        ib.qualifyContracts(self.call_contract)

        self.put_contract = put_position if not pd.isna(put_position) else res.get_contract('P', 2000)
        ib.qualifyContracts(self.put_contract)

        self.call_option_price = ib.reqMktData(self.call_contract, '', False,
                                               False)  # start data collection for calls
        self.put_option_price = ib.reqMktData(self.put_contract, '', False, False)  # start data collection for puts
        return

    @staticmethod
    def connect():
        ib.disconnect()
        ib.connect('127.0.0.1', 7497, clientId=np.random.randint(10, 1000))
        ib.client.MaxRequests = 55
        print('reconnected')

    @staticmethod
    def roll_contract(option_vol, value):
        option_vol = np.roll(option_vol, -1)
        option_vol[-1] = value
        return option_vol

    def account_update(self, value=None):

        self.update += 1
        self.cash_in_hand = float(value.value) if value.tag == 'TotalCashValue' else self.cash_in_hand

        self.portfolio_value = float(value.value) if value.tag == 'GrossPositionValue' else self.portfolio_value
        self.unrealizedPNL = float(value.value) if value.tag == 'UnrealizedPnL' else self.unrealizedPNL
        self.realizedPNL = float(value.value) if value.tag == 'RealizedPnL' else self.realizedPNL
        # if self.update % 5 != 0:
        #     return
        # self.account = ib.accountSummary()

        if self.prev_cash != self.cash_in_hand:

            self.prev_cash = self.cash_in_hand
            if self.submitted == 1:
                self.submitted = 0
        if not self.unrealizedPNL == 0 and not self.stock_owned.any() > 0:
            self.option_position()


def is_time_between(begin_time, end_time, check_time=None):
    # If check time is not given, default to current UTC time
    check_time = check_time or datetime.now().time()
    if begin_time < end_time:
        return begin_time <= check_time <= end_time
    else:  # crosses midnight
        return check_time >= begin_time or check_time <= end_time


def main():
    trading = Trade()
    ib.positionEvent += trading.option_position
    ib.accountSummaryEvent += trading.account_update
    ib.errorEvent += trading.error
    trading.ES.updateEvent += trading.trade
    ib.run()


if __name__ == '__main__':
    today = datetime.today().weekday()
    ib = IB()
    res = get_data()


    # if ((today == 4 and datetime.now().hour > 14) or today == 5 or (today == 6 and
    #                                                                          datetime.now().hour < 15)):
    #     print('No market now, wait until Sunday at 15:00')
    #     schedule.every().monday.at("15:00").do(main)
    #
    # elif 14 < datetime.now().hour < 15:
    #     print('No market now, wait until 15:00')
    #     schedule.every().day.at("15:00").do(main)

    try:
        main()
        schedule.run_pending()
        ib.sleep(1)
    except Exception as e:
        print(e)
        ib.disconnect()
    except KeyboardInterrupt:
        print('User stopped running')
        ib.disconnect()
