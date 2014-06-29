"""
Scrapy logging facility

See documentation in docs/topics/logging.rst
"""
import sys
import logging
import warnings

from twisted.python import log
from twisted.python import syslog

import scrapy
from scrapy.utils.python import unicode_to_str
from scrapy.settings import overridden_settings

# Logging levels
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL
SILENT = CRITICAL + 1

level_names = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
    SILENT: "SILENT",
}

class ScrapyFileLogObserver(log.FileLogObserver):

    def __init__(self, f, level=INFO, encoding='utf-8', crawler=None):
        self.level = level
        self.encoding = encoding
        if crawler:
            self.crawler = crawler
            self.emit = self._emit_with_crawler
        else:
            self.emit = self._emit
        log.FileLogObserver.__init__(self, f)

    def _emit(self, eventDict):
        ev = _adapt_eventdict(eventDict, self.level, self.encoding)
        if ev is not None:
            log.FileLogObserver.emit(self, ev)
            request = ev.get('request')
            if request:
                snapshot_id = request.meta.get('politepol_snapshot_id')
                if snapshot_id:
                    constrings = request.meta.get('politepol_constrings')
                    ev['format'] = 'Scrapy: ' + ev['format'] 
                    snapshot_trace(constrings, snapshot_id, -ev['logLevel'] if ev['logLevel'] >= ERROR else ev['logLevel'], log.textFromEventDict(ev))                        
        return ev

    def _emit_with_crawler(self, eventDict):
        ev = self._emit(eventDict)
        if ev:
            level = ev['logLevel']
            sname = 'log_count/%s' % level_names.get(level, level)
            self.crawler.stats.inc_value(sname)
            
from sqlalchemy import engine, Boolean, Column, DateTime, Integer, String, Table, Text, Time
from sqlalchemy.sql import select
from datetime import datetime

from sqlalchemy.ext.declarative import declarative_base
Base = declarative_base()
traces = Table(
    'traces', Base.metadata,
    Column('snapshot_id', Integer, nullable=False),
    Column('code', Integer, nullable=False),
    Column('message', Text, nullable=False),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False)
)

trace_engines = None  
def snapshot_trace(constrings, snapshot_id, code, message):
    global trace_engines
    if trace_engines is None:
         trace_engines = [engine.create_engine(constr, echo=False) for constr in constrings]
    
    now = datetime.utcnow()
    for engine_instance in trace_engines:
        conn = engine_instance.connect()                
        try:
            result = conn.execute(traces.insert().values(snapshot_id=snapshot_id, code=code, message=message, created_at=now, updated_at=now))
        finally:
            conn.close()

class ScrapySyslogObserver(syslog.SyslogObserver):

    def __init__(self, level=INFO, encoding='utf-8', crawler=None):
        self.level = level
        self.encoding = encoding
        prefix = 'scrapy'
        if crawler:
            self.crawler = crawler
            self.emit = self._emit_with_crawler
        else:
            self.emit = self._emit
        syslog.SyslogObserver.__init__(self, 'scrapy')

    def _emit(self, eventDict):
        ev = _adapt_eventdict(eventDict, self.level, self.encoding, False)
        if ev is not None:
            syslog.SyslogObserver.emit(self, ev)
        return ev

    def _emit_with_crawler(self, eventDict):
        ev = self._emit(eventDict)
        if ev:
            level = ev['logLevel']
            sname = 'log_count/%s' % level_names.get(level, level)
            self.crawler.stats.inc_value(sname)

def _adapt_eventdict(eventDict, log_level=INFO, encoding='utf-8', prepend_level=True):
    """Adapt Twisted log eventDict making it suitable for logging with a Scrapy
    log observer. It may return None to indicate that the event should be
    ignored by a Scrapy log observer.

    `log_level` is the minimum level being logged, and `encoding` is the log
    encoding.
    """
    ev = eventDict.copy()
    if ev['isError']:
        ev.setdefault('logLevel', ERROR)

    # ignore non-error messages from outside scrapy
    if ev.get('system') != 'scrapy' and not ev['isError']:
        return

    level = ev.get('logLevel')
    if level < log_level:
        return

    spider = ev.get('spider')
    if spider:
        ev['system'] = unicode_to_str(spider.name, encoding)

    lvlname = level_names.get(level, 'NOLEVEL')
    message = ev.get('message')
    if message:
        message = [unicode_to_str(x, encoding) for x in message]
        if prepend_level:
            message[0] = "%s: %s" % (lvlname, message[0])
        ev['message'] = message

    why = ev.get('why')
    if why:
        why = unicode_to_str(why, encoding)
        if prepend_level:
            why = "%s: %s" % (lvlname, why)
        ev['why'] = why

    fmt = ev.get('format')
    if fmt:
        fmt = unicode_to_str(fmt, encoding)
        if prepend_level:
            fmt = "%s: %s" % (lvlname, fmt)
        ev['format'] = fmt

    return ev

def _get_log_level(level_name_or_id):
    if isinstance(level_name_or_id, int):
        return level_name_or_id
    elif isinstance(level_name_or_id, basestring):
        return globals()[level_name_or_id]
    else:
        raise ValueError("Unknown log level: %r" % level_name_or_id)

def start(logfile=None, loglevel='INFO', logstdout=True, logencoding='utf-8', crawler=None):
    loglevel = _get_log_level(loglevel)
    file = open(logfile, 'a') if logfile else sys.stderr
    obs = ScrapyFileLogObserver(file, loglevel, logencoding, crawler)
    syslog_obs = ScrapySyslogObserver(loglevel, logencoding, crawler)

    _oldshowwarning = warnings.showwarning
    log.startLoggingWithObserver(obs.emit, setStdout=logstdout)
    log.addObserver(syslog_obs.emit)
    # restore warnings, wrongly silenced by Twisted
    warnings.showwarning = _oldshowwarning
    return obs

def msg(message=None, _level=INFO, **kw):
    kw['logLevel'] = kw.pop('level', _level)
    kw.setdefault('system', 'scrapy')
    if message is None:
        log.msg(**kw)
    else:
        log.msg(message, **kw)

def err(_stuff=None, _why=None, **kw):
    kw['logLevel'] = kw.pop('level', ERROR)
    kw.setdefault('system', 'scrapy')
    log.err(_stuff, _why, **kw)

def start_from_settings(settings, crawler=None):
    if settings.getbool('LOG_ENABLED'):
        return start(settings['LOG_FILE'], settings['LOG_LEVEL'], settings['LOG_STDOUT'],
            settings['LOG_ENCODING'], crawler)

def scrapy_info(settings):
    sflo = start_from_settings(settings)
    if sflo:
        msg("Scrapy %s started (bot: %s)" % (scrapy.__version__, \
            settings['BOT_NAME']))

        msg("Optional features available: %s" % ", ".join(scrapy.optional_features),
            level=INFO)

        d = dict(overridden_settings(settings))
        msg(format="Overridden settings: %(settings)r", settings=d, level=INFO)

        sflo.stop()

def start_from_crawler(crawler):
    return start_from_settings(crawler.settings, crawler)
