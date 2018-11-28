# -*- coding: utf-8 -*- {{{
# vim: set fenc=utf-8 ft=python sw=4 ts=4 sts=4 et:
#
# Copyright 2018, 8minutenergy Renewables
#
# Licensed under the Apache License, Version 2.0 (the "License"); you
# may not use this file except in compliance with the License. You may
# obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied. See the License for the specific language governing
# permissions and limitations under the License.
# }}}

import ast
import contextlib
import logging

import pytz
import psycopg2
from psycopg2 import InterfaceError, ProgrammingError, errorcodes
from psycopg2.sql import Identifier, Literal, SQL

from volttron.platform.agent import utils
from volttron.platform.agent import json as jsonapi

from .basedb import DbDriver

utils.setup_logging()
_log = logging.getLogger(__name__)


# There is a possible for race conditions if multiple clients
# insert simultaneously. There should only be one historian
# writing to the database, so the risk is acceptably low.
# Certian techniques are used to avoid issues with duplicate
# data, like ordering queries by ID when being fed to
# dictionaries and performing distinct queries.

"""
Implementation of PostgreSQL database operation for
:py:class:`sqlhistorian.historian.SQLHistorian` and
:py:class:`sqlaggregator.aggregator.SQLAggregateHistorian`
For method details please refer to base class
:py:class:`volttron.platform.dbutils.basedb.DbDriver`
"""
class RedshiftFuncts(DbDriver):
    def __init__(self, connect_params, table_names):
        if table_names:
            self.data_table = table_names['data_table']
            self.topics_table = table_names['topics_table']
            self.meta_table = table_names['meta_table']
            self.agg_topics_table = table_names.get('agg_topics_table')
            self.agg_meta_table = table_names.get('agg_meta_table')
        def connect():
            connection = psycopg2.connect(**connect_params)
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute('SET TIME ZONE UTC')
            return connection
        connect.__name__ = 'psycopg2'
        super(self.__class__, self).__init__(connect)

    @contextlib.contextmanager
    def bulk_insert(self):
        """
        This function implements the bulk insert requirements for Redshift historian by overriding the
        DbDriver::bulk_insert() in basedb.py and yields nescessary data insertion method needed for bulk inserts

        :yields: insert method
        """
        records = []

        def insert_data(ts, topic_id, data):
            """
            Inserts data records to the list

            :param ts: time stamp
            :type string
            :param topic_id: topic ID
            :type string
            :param data: data value
            :type any valid JSON serializable value
            :return: Returns True after insert
            :rtype: bool
            """
            value = jsonapi.dumps(data)
            records.append(SQL('({}, {}, {})').format(Literal(ts), Literal(topic_id), Literal(value)))
            return True

        yield insert_data

        if records:
            query = SQL('INSERT INTO {} VALUES {} ').format(
                Identifier(self.data_table), SQL(', ').join(records))
            self.execute_stmt(query)

    def rollback(self):
        try:
            return super(self.__class__, self).rollback()
        except InterfaceError:
            return False

    def setup_historian_tables(self):
        self.execute_stmt(SQL(
            'CREATE TABLE IF NOT EXISTS {} ('
                'ts TIMESTAMP SORTKEY NOT NULL, '
                'topic_id INTEGER NOT NULL, '
                'value_string TEXT NOT NULL, '
                'UNIQUE (topic_id, ts)'
            ')').format(Identifier(self.data_table)))
        self.execute_stmt(SQL(
            'CREATE TABLE IF NOT EXISTS {} ('
                'topic_id INTEGER IDENTITY (1, 1) PRIMARY KEY NOT NULL, '
                'topic_name VARCHAR(512) NOT NULL, '
                'UNIQUE (topic_name)'
            ')').format(Identifier(self.topics_table)))
        self.execute_stmt(SQL(
            'CREATE TABLE IF NOT EXISTS {} ('
                'topic_id INTEGER PRIMARY KEY NOT NULL, '
                'metadata TEXT NOT NULL'
            ')').format(Identifier(self.meta_table)))
        self.commit()

    def record_table_definitions(self, tables_def, meta_table_name):
        meta_table = Identifier(meta_table_name)
        self.execute_stmt(SQL(
            'CREATE TABLE IF NOT EXISTS {} ('
                'table_id VARCHAR(512) PRIMARY KEY NOT NULL, '
                'table_name VARCHAR(512) NOT NULL'
            ')').format(meta_table))
        update_stmt = SQL('UPDATE {} SET table_name = %(name)s '
                          'WHERE table_id = %(key)s').format(meta_table)
        insert_stmt = SQL('INSERT INTO {} '
                          'VALUES (%(key)s, %(name)s)').format(meta_table)
        tables_names = tables_def.copy()
        tables_names[''] = tables_names.pop('table_prefix', '')
        with self.cursor() as cursor:
            for key, name in tables_def.items():
                params = {'key': key, 'name': name}
                cursor.execute(update_stmt, params)
                if not cursor.rowcount:
                    cursor.execute(insert_stmt, params)

    def read_tablenames_from_db(self, meta_table_name):
        tables = dict(self.select(
            SQL('SELECT table_id, table_name FROM {}').format(
                Identifier(meta_table_name))))
        prefix = tables.pop('', '')
        tables['agg_topics_table'] = 'aggregate_' + tables['topics_table']
        tables['agg_meta_table'] = 'aggregate_' + tables['meta_table']
        if prefix:
            tables = {key: prefix + '_' + name for key, name in tables.items()}
        return tables

    def setup_aggregate_historian_tables(self, meta_table_name):
        table_names = self.read_tablenames_from_db(meta_table_name)
        self.data_table = table_names['data_table']
        self.topics_table = table_names['topics_table']
        self.meta_table = table_names['meta_table']
        self.agg_topics_table = table_names['agg_topics_table']
        self.agg_meta_table = table_names['agg_meta_table']
        self.execute_stmt(SQL(
            'CREATE TABLE IF NOT EXISTS {} ('
                'agg_topic_id INTEGER IDENTITY (1, 1) PRIMARY KEY NOT NULL, '
                'agg_topic_name VARCHAR(512) NOT NULL, '
                'agg_type VARCHAR(512) NOT NULL, '
                'agg_time_period VARCHAR(512) NOT NULL, '
                'UNIQUE (agg_topic_name, agg_type, agg_time_period)'
            ')').format(Identifier(self.agg_topics_table)))
        self.execute_stmt(SQL(
            'CREATE TABLE IF NOT EXISTS {} ('
                'agg_topic_id INTEGER PRIMARY KEY NOT NULL, '
                'metadata TEXT NOT NULL'
            ')').format(Identifier(self.agg_meta_table)))
        self.commit()

    def query(self, topic_ids, id_name_map, start=None, end=None, skip=0,
              agg_type=None, agg_period=None, count=None,
              order='FIRST_TO_LAST'):
        if agg_type and agg_period:
            table_name = agg_type + '_' + agg_period
        else:
            table_name = self.data_table
        topic_id = Literal(0)
        query = [SQL(
            '''SELECT DISTINCT to_char(ts, 'YYYY-MM-DD"T"HH24:MI:SS.USOF:00'), '''
            'value_string\n'
            'FROM {}\n'
            'WHERE topic_id = {}'
        ).format(Identifier(table_name), topic_id)]
        if start and start.tzinfo != pytz.UTC:
            start = start.astimezone(pytz.UTC)
        if end and end.tzinfo != pytz.UTC:
            end = end.astimezone(pytz.UTC)
        if start and start == end:
            query.append(SQL(' AND ts = {}').format(Literal(start)))
        else:
            if start:
                query.append(SQL(' AND ts >= {}').format(Literal(start)))
            if end:
                query.append(SQL(' AND ts < {}').format(Literal(end)))
        query.append(SQL('ORDER BY ts {}'.format(
            'DESC' if order == 'LAST_TO_FIRST' else 'ASC')))
        if skip or count:
            query.append(SQL('LIMIT {} OFFSET {}').format(
                Literal(None if not count or count < 0 else count),
                Literal(None if not skip or skip < 0 else skip)))
        query = SQL('\n').join(query)
        values = {}
        for topic_id._wrapped in topic_ids:
            name = id_name_map[topic_id.wrapped]
            with self.select(query, fetch_all=False) as cursor:
                values[name] = [(ts, jsonapi.loads(value))
                                for ts, value in cursor]
        return values

    def insert_topic(self, topic):
        with self.cursor() as cursor:
            cursor.execute(self.insert_topic_query(), {'topic': topic})
            return cursor.next()[0]

    def insert_agg_topic(self, topic, agg_type, agg_time_period):
        with self.cursor() as cursor:
            cursor.execute(self.insert_agg_topic_stmt(),
                           {'topic': topic,
                            'type': agg_type,
                            'period': agg_time_period})
            return cursor.next()[0]

    def insert_meta_query(self):
        return SQL('INSERT INTO {} VALUES (%s, %s)').format(
            Identifier(self.meta_table))

    def insert_data_query(self):
        return SQL('INSERT INTO {} VALUES (%s, %s, %s)').format(
            Identifier(self.data_table))

    def insert_topic_query(self):
        return SQL(
            'INSERT INTO {0} (topic_name) VALUES (%(topic)s); '
            'SELECT MAX(topic_id) FROM {0} '
            'WHERE topic_name = %(topic)s').format(
            Identifier(self.topics_table))

    def update_topic_query(self):
        return SQL(
            'UPDATE {} SET topic_name = %s '
            'WHERE topic_id = %s').format(Identifier(self.topics_table))

    def get_aggregation_list(self):
        return ['AVG', 'MIN', 'MAX', 'COUNT', 'SUM', 'BIT_AND', 'BIT_OR',
                'BOOL_AND', 'BOOL_OR', 'MEDIAN', 'STDDEV', 'STDDEV_POP',
                'STDDEV_SAMP', 'VAR_POP', 'VAR_SAMP', 'VARIANCE']

    def insert_agg_topic_stmt(self):
        return SQL(
            'INSERT INTO {0} (agg_topic_name, agg_type, agg_time_period) '
            'VALUES (%(topic)s, %(type)s, %(period)s); '
            'SELECT MAX(agg_topic_id) FROM {0}'
            'WHERE agg_topic_name = %(topic)s AND '
                'agg_type = %(type)s AND '
                'agg_time_period = %(period)s'
        ).format(Identifier(self.agg_topics_table))

    def update_agg_topic_stmt(self):
        return SQL(
            'UPDATE {} SET agg_topic_name = %s '
            'WHERE agg_topic_id = %s').format(
            Identifier(self.agg_topics_table))

    def replace_agg_meta_stmt(self):
        return SQL('INSERT INTO {} VALUES (%s, %s)').format(
            Identifier(self.agg_meta_table))

    def get_topic_map(self):
        query = SQL(
            'SELECT topic_id, topic_name, LOWER(topic_name) '
            'FROM {} '
            'ORDER BY topic_id').format(Identifier(self.topics_table))
        rows = self.select(query)
        id_map = {key: tid for tid, _, key in rows}
        name_map = {key: name for _, name, key in rows}
        return id_map, name_map

    def get_agg_topics(self):
        query = SQL(
            'SELECT agg_topic_name, agg_type, agg_time_period, metadata '
            'FROM {} as t, {} as m '
            'WHERE t.agg_topic_id = m.agg_topic_id '
            'ORDER BY t.agg_topic_id').format(
            Identifier(self.agg_topics_table), Identifier(self.agg_meta_table))
        try:
            rows = self.select(query)
        except ProgrammingError as exc:
            if exc.pgcode == errorcodes.UNDEFINED_TABLE:
                return []
            raise
        return [(name, type_, tp, ast.literal_eval(meta)['configured_topics'])
                for name, type_, tp, meta in rows]

    def get_agg_topic_map(self):
        query = SQL(
            'SELECT agg_topic_id, LOWER(agg_topic_name), '
                'agg_type, agg_time_period '
            'FROM {} '
            'ORDER BY agg_topic_id').format(Identifier(self.agg_topics_table))
        try:
            rows = self.select(query)
        except ProgrammingError as exc:
            if exc.pgcode == errorcodes.UNDEFINED_TABLE:
                return {}
            raise
        return {(name, type_, tp): id_ for id_, name, type_, tp in rows}

    def query_topics_by_pattern(self, topic_pattern):
        query = SQL(
            'SELECT topic_name, topic_id '
            'FROM {} '
            'WHERE topic_name ~* %s '
            'ORDER BY topic_id').format(Identifier(self.topics_table))
        return dict(self.select(query, (topic_pattern,)))

    def create_aggregate_store(self, agg_type, agg_time_period):
        table_name = agg_type + '_' + agg_time_period
        self.execute_stmt(SQL(
            'CREATE TABLE IF NOT EXISTS {} ('
                'ts TIMESTAMP SORTKEY NOT NULL, '
                'topic_id INTEGER NOT NULL, '
                'value_string TEXT NOT NULL, '
                'topics_list TEXT, '
                'UNIQUE (ts, topic_id)'
            ')').format(Identifier(table_name)), commit=True)

    def insert_aggregate_stmt(self, table_name):
        return SQL('INSERT INTO {} VALUES (%s, %s, %s, %s)').format(
            Identifier(table_name))

    def collect_aggregate(self, topic_ids, agg_type, start=None, end=None):
        if (isinstance(agg_type, str) and
                agg_type.upper() not in self.get_aggregation_list()):
            raise ValueError('Invalid aggregation type {}'.format(agg_type))
        query = [
            SQL('SELECT {}(CAST(value_string as float)), COUNT(value_string)'.format(
                agg_type.upper())),
            SQL('FROM {}').format(Identifier(self.data_table)),
            SQL('WHERE topic_id in ({})').format(
                SQL(', ').join(Literal(tid) for tid in topic_ids)),
        ]
        if start is not None:
            query.append(SQL(' AND ts >= {}').format(Literal(start)))
        if end is not None:
            query.append(SQL(' AND ts < {}').format(Literal(end)))
        rows = self.select(SQL('\n').join(query))
        return rows[0] if rows else (0, 0)
