import json

gt = json.load(open('agentic-bank-public/ground_truth.json', encoding='utf-8'))['scenarios']
sub = json.load(open('submission.json', encoding='utf-8'))['answers']

for sid in ['P1', 'P2', 'P3', 'P4', 'P5', 'P6']:
    if sid not in sub:
        print(f'{sid}: missing')
        continue
    
    score = 0
    gt_cov = gt[sid]['covenants']
    sub_cov = sub[sid]
    for cov_id in ['6.1', '6.2', '6.3']:
        s_ans = sub_cov.get(cov_id, {})
        g_ans = gt_cov[cov_id]
        
        status_match = s_ans.get('status') == g_ans['status']
        actual_match = False
        try:
            actual_match = abs(float(s_ans.get('actual', 0)) - g_ans['actual']) < 0.01
        except:
            pass
        
        g_ev = g_ans['evidence_txn_id']
        s_ev = s_ans.get('evidence_txn_id')
        ev_match = True
        if g_ev is not None:
            ev_match = (s_ev == g_ev)
            
        if status_match and actual_match and ev_match:
            score += 1
        else:
            print(f'{sid} {cov_id} FAILED:')
            print(f'  Expected: {g_ans}')
            print(f'  Got     : {s_ans.get("status")}, {s_ans.get("actual")}, {s_ans.get("evidence_txn_id")}')
            
    print(f'{sid}: {score}/3 covenants correct')
