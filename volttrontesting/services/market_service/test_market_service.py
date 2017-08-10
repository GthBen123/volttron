# -*- coding: utf-8 -*- {{{
# vim: set fenc=utf-8 ft=python sw=4 ts=4 sts=4 et:

# Copyright (c) 2016, Battelle Memorial Institute
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in
#    the documentation and/or other materials provided with the
#    distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# The views and conclusions contained in the software and documentation
# are those of the authors and should not be interpreted as representing
# official policies, either expressed or implied, of the FreeBSD
# Project.
#
# This material was prepared as an account of work sponsored by an
# agency of the United States Government.  Neither the United States
# Government nor the United States Department of Energy, nor Battelle,
# nor any of their employees, nor any jurisdiction or organization that
# has cooperated in the development of these materials, makes any
# warranty, express or implied, or assumes any legal liability or
# responsibility for the accuracy, completeness, or usefulness or any
# information, apparatus, product, software, or process disclosed, or
# represents that its use would not infringe privately owned rights.
#
# Reference herein to any specific commercial product, process, or
# service by trade name, trademark, manufacturer, or otherwise does not
# necessarily constitute or imply its endorsement, recommendation, or
# favoring by the United States Government or any agency thereof, or
# Battelle Memorial Institute. The views and opinions of authors
# expressed herein do not necessarily state or reflect those of the
# United States Government or any agency thereof.
#
# PACIFIC NORTHWEST NATIONAL LABORATORY
# operated by BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
# under Contract DE-AC05-76RL01830
# }}}

"""
Pytest test cases for testing market service agent.
"""

import logging
import gevent
import pytest

from volttron.platform.agent import utils
from volttron.platform.agent.base_market_agent import MarketAgent
from volttron.platform.agent.base_market_agent.poly_line import PolyLine
from volttron.platform.agent.base_market_agent.point import Point
from volttron.platform.agent.base_market_agent.buy_sell import BUYER, SELLER

STANDARD_GET_TIMEOUT = 5
_log = logging.getLogger(__name__)
utils.setup_logging()

class _config_test_agent(MarketAgent):
    def __init__(self, **kwargs):
        super(_config_test_agent, self).__init__(**kwargs)
        self.reset_results()
        self.wants_reservation = True

    def reset_results(self):
        self.reservation_callback_results = []
        self.offer_callback_results = []
        self.aggregate_callback_results = []
        self.price_callback_results = []
        self.error_callback_results = []

    def join_market_as_simple_seller(self, market_name):
        self.join_market(market_name, SELLER, self.reservation_callback, self.offer_callback, None, self.price_callback, self.error_callback)

    def join_market_as_simple_buyer(self, market_name):
        self.join_market(market_name, BUYER, self.reservation_callback, self.offer_callback, None, None, self.error_callback)

    def reservation_callback(self, timestamp, market_name, buyer_seller):
        self.reservation_callback_results.append((timestamp, market_name, buyer_seller, self.wants_reservation))
        return self.wants_reservation

    def offer_callback(self, timestamp, market_name, buyer_seller):
        if buyer_seller == BUYER:
            curve = self.create_demand_curve()
        else:
            curve = self.create_supply_curve()
        self.offer_callback_results.append((timestamp, market_name, buyer_seller, curve))
        self.make_offer(market_name, buyer_seller, curve)

    def create_supply_curve(self):
        supply_curve = PolyLine()
        price = 100
        quantity = 0
        supply_curve.add(Point(price,quantity))
        price = 100
        quantity = 1000
        supply_curve.add(Point(price,quantity))
        return supply_curve

    def create_demand_curve(self):
        demand_curve = PolyLine()
        price = 0
        quantity = 1000
        demand_curve.add(Point(price, quantity))
        price = 1000
        quantity = 0
        demand_curve.add(Point(price, quantity))
        return demand_curve

    def aggregate_callback(self, timestamp, market_name, buyer_seller, curve):
        self.aggregate_callback_results.append((timestamp, market_name, buyer_seller, curve))

    def price_callback(self, timestamp, market_name, buyer_seller, price, quantity):
        self.price_callback_results.append((timestamp, market_name, buyer_seller, price, quantity))

    def error_callback(self, timestamp, market_name, buyer_seller, error_message):
        self.error_callback_results.append((timestamp, market_name, buyer_seller, error_message))


@pytest.fixture(scope="module")
def _module_config_test_service(request, volttron_instance):
    # Start the market service agent
    market_service_uuid = volttron_instance.install_agent(
        agent_dir="services/core/MarketServiceAgent",
        config_file={
            'market_period': 3,
            'reservation_delay': 0,
            'offer_delay': 1,
            'clear_delay': 1
        },
        start=True)
    _log.debug("market service agent id: ", market_service_uuid)
    yield market_service_uuid
    _log.debug("In market service agent teardown method of module")
    volttron_instance.stop_agent(market_service_uuid)


@pytest.fixture(scope="function")
def _function_config_test_seller(request, volttron_instance, _module_config_test_service):
    seller_agent = volttron_instance.build_agent(identity='config_test_seller', agent_class=_config_test_agent)
    yield seller_agent
    seller_agent.core.stop(timeout=STANDARD_GET_TIMEOUT)

@pytest.fixture(scope="function")
def _function_config_test_buyer(request, volttron_instance, _module_config_test_service):
    buyer_agent = volttron_instance.build_agent(identity='config_test_buyer', agent_class=_config_test_agent)
    yield buyer_agent
    buyer_agent.core.stop(timeout=STANDARD_GET_TIMEOUT)


@pytest.mark.market
def test_simple_market_reservations(_function_config_test_seller, _function_config_test_buyer):
    seller_agent = _function_config_test_seller
    buyer_agent = _function_config_test_buyer
    market_name = 'electricity'
    seller_agent.join_market_as_simple_seller(market_name)
    buyer_agent.join_market_as_simple_buyer(market_name)
    gevent.sleep(1)
    assert len(seller_agent.reservation_callback_results) == 1, "expected that the seller got a reservation callback"
    assert len(buyer_agent.reservation_callback_results) == 1, "expected that the buyer got a reservation callback"

@pytest.mark.market
def test_simple_market_offers(_function_config_test_seller, _function_config_test_buyer):
    seller_agent = _function_config_test_seller
    buyer_agent = _function_config_test_buyer
    market_name = 'electricity'
    seller_agent.join_market_as_simple_seller(market_name)
    buyer_agent.join_market_as_simple_buyer(market_name)
    gevent.sleep(2)
    assert len(seller_agent.offer_callback_results) == 1, "expected that the seller got an offer callback"
    assert len(buyer_agent.offer_callback_results) == 1, "expected that the buyer got an offer callback"

