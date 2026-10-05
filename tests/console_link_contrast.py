"""Refs #114: computed-color checks on the real isolated console, not pixels."""
import json

from playwright.sync_api import expect

LEGACY = "body[arco-theme=dark] .ai-dialog a,body[arco-theme=dark] .ai-dialog a:hover,body[arco-theme=dark] .ai-dialog a:focus-visible{color:#2563eb!important}"

SAMPLE = r"""e => {
  const rgba=s=>{
    if(!/^rgba?\(/.test(s)) throw new Error('unsupported computed color');
    const values=(s.match(/[\d.]+/g)||[]).map(Number);
    if(values.length===3) values.push(1);
    if(values.length!==4 || values.some(v=>!Number.isFinite(v))) throw new Error('invalid computed color');
    return values;
  };
  const blend=(color,bg)=>color.slice(0,3).map((v,i)=>v*color[3]+bg[i]*(1-color[3]));
  const walk=document.createTreeWalker(e,NodeFilter.SHOW_TEXT);
  let node;while((node=walk.nextNode()) && !node.textContent.trim()) {}
  if(!node) throw new Error('link has no text');
  const text=node.parentElement,layers=[];
  for(let current=text;current;current=current.parentElement){
    const css=getComputedStyle(current);
    if(css.backgroundImage!=='none'||css.filter!=='none'||css.mixBlendMode!=='normal'||Number(css.opacity)!==1)
      throw new Error('unsupported compositing needs explicit review');
    layers.push(rgba(css.backgroundColor));
  }
  const css=getComputedStyle(text),bg=layers.reverse().reduce((value,layer)=>blend(layer,value),[255,255,255]);
  const rect=e.getBoundingClientRect();
  return {foreground:blend(rgba(css.color),bg),effective_background:bg,
    computed_color:css.color,font_size:css.fontSize,font_weight:css.fontWeight,
    decoration_line:css.textDecorationLine,href:e.getAttribute('href'),text:e.textContent.trim(),
    tab_index:e.tabIndex,disabled:!!e.closest('[aria-disabled=true],[inert]')||e.hasAttribute('disabled'),
    tag:e.tagName,theme:document.body.getAttribute('arco-theme'),
    focus:e.matches(':focus'),hover:e.matches(':hover'),
    rect:{x:rect.x,y:rect.y,width:rect.width,height:rect.height}};
}"""


def luminance(rgb):
    channels = [value / 255 for value in rgb]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))


def contrast(foreground, background):
    low, high = sorted((luminance(foreground), luminance(background)))
    return (high + .05) / (low + .05)


def sample(link):
    value = link.evaluate(SAMPLE)
    value['contrast'] = contrast(value['foreground'], value['effective_background'])
    return value


def verify_console_links(harness, tab, theme):
    p = harness.page
    dialog = p.locator('.ai-dialog')
    targets = [('task-status', dialog.get_by_role('link', name='任务状态', exact=True)),
               ('deployment', dialog.get_by_role('link', name='部署状态', exact=True))]
    if tab == 'sources':
        targets += [('source', dialog.get_by_role('link', name='查看订阅地址 ↗', exact=True).first),
                    ('subscription', dialog.get_by_role('link', name='打开此订阅 →', exact=True).first)]
    records = []
    output = harness.out / f'link-contrast-{tab}.json'
    expected = 'rgb(147, 197, 253)' if theme == 'dark' else 'rgb(37, 99, 235)'
    try:
        for name, link in targets:
            expect(link).to_have_count(1)
            link.evaluate("e=>e.scrollIntoView({block:'center',inline:'nearest'})")
            p.mouse.move(0, 0)
            link.evaluate('(e)=>e.blur()')
            expect(link).to_be_visible()
            record = {'target': name, 'legacy_color_controls': {}, 'states': {}}
            records.append(record)
            for state in ('normal', 'hover', 'focus'):
                if state == 'normal':
                    p.mouse.move(0, 0);link.evaluate('(e)=>e.blur()')
                elif state == 'hover':
                    link.hover()
                else:
                    p.mouse.move(0, 0);link.focus();expect(link).to_be_focused()
                legacy = p.add_style_tag(content=LEGACY) if theme == 'dark' else None
                expect(link).to_have_css('color', 'rgb(37, 99, 235)')
                before = sample(link)
                record['legacy_color_controls'][state] = before
                if legacy:
                    if state == 'normal':
                        harness.check(f'{tab}_{name}_old_dark_color_negative', before['contrast'] < 4.5)
                    p.screenshot(path=str(harness.out / f'link-{tab}-{name}-{state}-legacy.png'))
                    legacy.evaluate('(e)=>e.remove()')
                else:
                    harness.check(f'{tab}_{name}_{state}_original_light_color_positive', before['contrast'] >= 4.5)
                # Observe the intended final CSS color, including any transition.
                # This is a bounded condition; no fixed settling sleep is used.
                expect(link).to_have_css('color', expected)
                current = sample(link)
                record['states'][state] = current
                harness.check(f'{tab}_{name}_{state}_ordinary_active_link', current['tag'] == 'A' and
                              not current['disabled'] and current['tab_index'] >= 0 and current['theme'] == theme)
                harness.check(f'{tab}_{name}_{state}_minimum_contrast', current['contrast'] >= 4.5)
                harness.check(f'{tab}_{name}_{state}_content_and_font_preserved',
                              all(current[key] == before[key] for key in ('href', 'text', 'font_size', 'font_weight', 'decoration_line')))
                if state == 'focus':
                    harness.check(f'{tab}_{name}_focus_reachable', current['focus'])
                if state == 'hover':
                    harness.check(f'{tab}_{name}_hover_applied', current['hover'])
                p.screenshot(path=str(harness.out / f'link-{tab}-{name}-{state}.png'))
    finally:
        output.write_text(json.dumps({'theme': theme, 'tab': tab, 'computed_browser_colors': True,
                                     'legacy_control_is_isolated_css_injection': True,
                                     'records': records}, ensure_ascii=False, indent=2))
