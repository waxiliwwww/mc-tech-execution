"""One log matcher for job and standalone diagnostics; wrapped lines without duplicates."""
import re

def match_lines(lines, rules):
    lines=list(lines); findings=[]
    def matches(text):
        found=[r for r in rules if re.search(r['pattern'],text,re.I)]
        if any(r['category']=='tally_material_reference' for r in found):
            found=[r for r in found if r['category']!='missing_input_data']
        return found
    for pos,(number,line) in enumerate(lines):
        text=line.strip();hit=matches(text); end=number
        if not hit and pos+1<len(lines) and not matches(lines[pos+1][1]):
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
