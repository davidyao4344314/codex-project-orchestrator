"""Drag task cards between declared agent lanes."""

import streamlit.components.v2 as components

board = components.component(
    "agent_task_board",
    js="""
export default function({data, parentElement, setTriggerValue}) {
    const root = document.createElement('div');
    Object.assign(root.style, {display:'flex',gap:'12px',overflowX:'auto',padding:'8px 0'});
    for (const agent of data.agents) {
        const lane = document.createElement('section');
        Object.assign(lane.style,{minWidth:'220px',flex:'1',background:'#edf0e8',
            padding:'12px',borderRadius:'12px',border:'1px solid #cdd8cc'});
        const title = document.createElement('strong'); title.textContent = agent.role;
        lane.appendChild(title);
        const hint = document.createElement('p');hint.textContent='Drop an assignment here';
        hint.style.fontSize='12px';lane.appendChild(hint);
        lane.ondragover = e => {if (!data.busy) e.preventDefault();};
        lane.ondrop = e => {
            e.preventDefault(); if (data.busy) return;
            const id=e.dataTransfer.getData('text/plain');
            if (data.tasks.some(t=>t.id===id && t.agent_id!==agent.id))
                setTriggerValue('move',{task:id,agent:agent.id});
        };
        for (const task of data.tasks.filter(t=>t.agent_id===agent.id)) {
            const card=document.createElement('div'); card.textContent=task.title;
            card.draggable=!data.busy;
            Object.assign(card.style,{padding:'12px',marginTop:'8px',background:'white',
                borderRadius:'8px',cursor:data.busy?'default':'grab',color:'#202d26'});
            card.ondragstart=e=>e.dataTransfer.setData('text/plain',task.id);
            lane.appendChild(card);
        }
        root.appendChild(lane);
    }
    parentElement.appendChild(root);
    return ()=>root.remove();
}
""",
)
