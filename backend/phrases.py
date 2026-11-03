"""Operations on timed lyric phrases without changing their sung words."""
import math
import re
from uuid import uuid4

UNIT = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+(?:'[A-Za-z]+)*")


def split_text(text, position):
    matches=list(UNIT.finditer(text))
    if not 0 < position < len(matches):
        raise ValueError('拆分位置必须在两个发音单位之间。')
    point=matches[position].start()
    return text[:point].strip(),text[point:].strip()


def split_phrase(project, segment_id, position, at):
    index=next((i for i,x in enumerate(project['lines']) if x['id']==segment_id),-1)
    if index<0:raise FileNotFoundError()
    line=project['lines'][index]
    if type(position) is not int or isinstance(at,bool) or not isinstance(at,(int,float)) or not math.isfinite(at):
        raise ValueError('拆分位置或时间无效。')
    if at-line['start']<.12-1e-9 or line['end']-at<.12-1e-9:
        raise ValueError('拆分后每句至少保留 120 毫秒。')
    if len(UNIT.findall(line['original']))!=len(UNIT.findall(line['text'])):
        raise ValueError('原词和新词发音单位数量不同，无法自动对应拆分位置，请先校正歌词。')
    first_original,second_original=split_text(line['original'],position)
    first_text,second_text=split_text(line['text'],position)
    first={**line,'original':first_original,'text':first_text,'end':at}
    second={**line,'id':uuid4().hex,'original':second_original,'text':second_text,'start':at}
    project['lines'][index:index+1]=[first,second]


def merge_next(project, segment_id):
    index=next((i for i,x in enumerate(project['lines']) if x['id']==segment_id),-1)
    if index<0:raise FileNotFoundError()
    if index+1>=len(project['lines']):raise ValueError('当前已经是最后一句，无法向后合并。')
    first,second=project['lines'][index:index+2]
    if first['mode']!=second['mode'] or first['restore']!=second['restore']:
        raise ValueError('两句生成模式不同，请先统一后再合并。')
    def join(a,b):
        return a+(' ' if a[-1:].isascii() and a[-1:].isalpha() and b[:1].isascii() and b[:1].isalpha() else '')+b
    merged={**first,'original':join(first['original'],second['original']),
            'text':join(first['text'],second['text']),'end':second['end'],
            'needs_review':bool(first.get('needs_review') or second.get('needs_review'))}
    project['lines'][index:index+2]=[merged]
