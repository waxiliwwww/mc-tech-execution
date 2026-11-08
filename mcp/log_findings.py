"""One log matcher for job and standalone diagnostics; wrapped lines without duplicates."""
import re

def failure_evidence(text):
    # Positive startup summaries often contain the word "errors". Remove only
    # their explicit negation, so a separate real failure on the line survives.
    text = re.sub(r'\b(?:no|zero|0)\s+(?:errors?|failures?)\b|\bwithout\s+errors?\b|\berror[- ]free\b', '', text, flags=re.I)
    return bool(re.search(r'\b(?:errors?|failed|failure|unable|cannot|invalid|missing)\b|could not|not found|does not exist', text, re.I))


def match_lines(lines, rules):
    lines=list(lines); findings=[]
    def matches(text):
        found=[r for r in rules if re.search(r['pattern'],text,re.I)
               and (not r.get('requires_failure') or failure_evidence(text))]
        # Resolve generic symptoms only within this same matched log span;
        # unrelated errors on other lines must remain independently visible.
        superseded = {category for rule in found for category in rule.get('supersedes', [])}
        found = [rule for rule in found if rule['category'] not in superseded]
        return found
    for pos,(number,line) in enumerate(lines):
        text=line.strip();hit=matches(text); end=number
        if not hit and failure_evidence(text) and pos+1<len(lines) and not matches(lines[pos+1][1]):
            text+=' '+lines[pos+1][1].strip();hit=matches(text);end=lines[pos+1][0]
        for rule in hit:
            if findings and findings[-1]['category']==rule['category'] and findings[-1]['line_end']>=number-1:
                findings[-1]['line_end']=end
                findings[-1]['occurrences']+=1
                continue
            findings.append({'line':number,'line_end':end,'occurrences':1,'excerpt':text[:1000],
                             'category':rule['category'],'suggestion':rule['suggestion'],
                             'rule_status':rule['status'],'reference':rule.get('reference')})
    return findings[:50]
