"""Exercise the built production viewer with real pointer events and exported height data."""
import json
import os
import re

import pytest
from PIL import ImageChops

from test_navigation_ui_browser import (navigation_page, navigation_bundle, navigation_server,
    open_navigation, make_route, VIEWER, expect)
from browser_support import load, screenshot
from csmodernmap.preview_scene import world_to_geo
from csmodernmap.web_export_v2 import payload_from_preview_bundle, render_export_html

pytestmark = [pytest.mark.browser, pytest.mark.skipif(os.environ.get('CSL_PREVIEW_BROWSER_TEST') != '1', reason='opt-in WebGL test')]


def instrument(source):
    # Expose the actual production map in test responses only, without a shipped diagnostics API.
    result, count = re.subn(r'new [\w$.]+\.Map\(\{container:[`\"]map[`\"]',
        lambda m: 'window.__testMap='+m.group(), source)
    assert count == 1
    return result


def expose_map(page):
    def handle(route):
        response = route.fetch()
        route.fulfill(response=response, body=instrument(response.text()))
    page.route('**/assets/index-*.js', handle)


def buildings(bundle, game='cs1'):
    features = []
    for i, (x, height) in enumerate([(50, 48), (140, 12), (230, None)], 1):
        props = {'featureId':f'building:{i}', 'buildingId':i, 'name':f'{game} building {i}',
            'height_m':height, 'base_elevation_m':-5 if height is not None else None,
            'height_source':f'{game}:generated-bounds' if game == 'cs1' else 'cs2:mesh-bounds'}
        features.append({'type':'Feature','properties':props,'geometry':{'type':'Polygon',
            'coordinates':[[world_to_geo(a,b) for a,b in [(x,-15),(x+30,-15),(x+30,15),(x,15),(x,-15)]]]}})
    (bundle.root/'buildings.geojson').write_text(json.dumps({'type':'FeatureCollection','features':features}),encoding='utf-8')
    bundle.manifest['sources']['buildings']='buildings.geojson?revision=1'
    bundle.manifest['sourceRevisions']['buildings']=1
    bundle.manifest['layers']['building']=True
    bundle.manifest['renderStack'].append('building')
    (bundle.root/'manifest.json').write_text(json.dumps(bundle.manifest),encoding='utf-8')


def camera(page):
    return page.evaluate('({pitch:__testMap.getPitch(),bearing:__testMap.getBearing(),zoom:__testMap.getZoom()})')


def settle_camera(page, pitch):
    page.wait_for_function('(pitch)=>!__testMap.isMoving() && Math.abs(__testMap.getPitch()-pitch)<.001', arg=pitch)


def drag(page, dx=0, dy=0):
    page.mouse.move(650,450); page.mouse.down(button='middle')
    page.mouse.move(650+dx,450+dy,steps=6); page.mouse.up(button='middle')
    page.wait_for_timeout(100)


@pytest.mark.parametrize('game',['cs1','cs2'])
def test_3d_camera_buildings_selection_and_refresh(navigation_page,navigation_bundle,navigation_server,game):
    page=navigation_page; buildings(navigation_bundle,game); expose_map(page)
    load(page,navigation_server,1)
    toggle=page.get_by_role('button',name='3D 预览',exact=True)
    expect(toggle).to_have_attribute('aria-pressed','false')
    flat=screenshot(page,f'{game}-height-2d')
    drag(page,50,-100); assert camera(page)['pitch']==0
    toggle.click(); settle_camera(page, 52)
    assert camera(page)['pitch']==pytest.approx(52)
    raised=screenshot(page,f'{game}-height-3d')
    assert ImageChops.difference(flat,raised).getbbox()
    expect(page.locator('.preview-3d-notice')).to_be_hidden()
    assert page.evaluate('new Set(__testMap.queryRenderedFeatures({layers:["buildings-3d"]}).map(f=>f.properties.buildingId)).size')==2
    roof = page.evaluate('''() => {
      for(let y=120;y<700;y+=8) for(let x=350;x<1000;x+=8) {
        const tall=__testMap.queryRenderedFeatures([x,y],{layers:['buildings-3d']});
        const ground=__testMap.queryRenderedFeatures([x,y],{layers:['buildings']});
        if(tall.length && !ground.length) return {x,y,id:tall[0].properties.buildingId};
      }
    }''')
    assert roof
    page.mouse.click(roof['x'],roof['y'])
    assert page.evaluate('window.events.filter(e=>e.type==="objectSelected").at(-1).properties.buildingId')==roof['id']
    page.evaluate('window.cslPreview.dispatch({type:"clearObjectSelection"})')
    drag(page,40,40)
    assert camera(page)['pitch']==pytest.approx(40)
    assert camera(page)['bearing']==pytest.approx(20)
    drag(page,0,-300); assert camera(page)['pitch']==pytest.approx(60)
    drag(page,0,250); assert camera(page)['pitch']==0
    drag(page,0,-100); assert camera(page)['pitch']==pytest.approx(30)
    page.mouse.move(650,450);page.mouse.down(button='middle');page.mouse.move(650,400)
    page.evaluate('window.dispatchEvent(new Event("blur"))'); stopped=camera(page)
    page.mouse.move(750,300);page.mouse.up(button='middle')
    assert camera(page)==stopped
    # An unrelated button release must not terminate the middle drag.
    page.mouse.move(650,450);page.mouse.down(button='middle')
    page.mouse.down(button='left');page.mouse.up(button='left');page.mouse.move(670,440);page.mouse.up(button='middle')
    saved=camera(page)
    page.evaluate('window.cslPreview.dispatch({type:"beginSelection"})')
    assert camera(page)['pitch']==0
    expect(toggle).to_be_disabled()
    page.keyboard.press('Escape'); assert camera(page)==saved
    page.evaluate('window.cslPreview.dispatch({type:"beginSelection"})')
    page.mouse.move(600,300);page.mouse.down();page.mouse.move(700,400);page.mouse.up()
    assert camera(page)==saved
    assert page.evaluate('window.events.some(e=>e.type==="selectionChanged")')
    # A layer/theme revision preserves the free camera.
    navigation_bundle.manifest['revision']=2; navigation_bundle.revision=2
    (navigation_bundle.root/'manifest.json').write_text(json.dumps(navigation_bundle.manifest),encoding='utf-8')
    load(page,navigation_server,2); assert camera(page)==saved
    page.evaluate('window.cslPreview.dispatch({type:"setOutputRotation",rotation:0})')
    assert camera(page)==saved
    theme = json.loads(json.dumps(navigation_bundle.manifest['theme']))
    theme['building'].update(colorMode='uniform',fill='#d02020')
    page.evaluate('theme=>window.cslPreview.dispatch({type:"applyTheme",theme})',theme)
    assert page.evaluate('__testMap.getPaintProperty("buildings-3d","fill-extrusion-color")')=='#d02020'
    page.evaluate('window.cslPreview.dispatch({type:"setLayerVisibility",layer:"building",visible:false})')
    assert not page.evaluate('!!__testMap.getLayer("buildings-3d")')
    page.evaluate('window.cslPreview.dispatch({type:"setLayerVisibility",layer:"building",visible:true})')
    assert page.evaluate('!!__testMap.getLayer("buildings-3d")')
    page.evaluate('window.cslPreview.dispatch({type:"setOutputRotation",rotation:90})')
    drag(page,40,0)
    toggle.click(); settle_camera(page, 0); assert camera(page)['pitch']==0 and camera(page)['bearing']==-90
    assert not page.evaluate('!!__testMap.getLayer("buildings-3d")')
    toggle.focus();page.keyboard.press('Space');expect(toggle).to_have_attribute('aria-pressed','true')
    page.evaluate('window.cslPreview.dispatch({type:"fitBounds"})');expect(toggle).to_have_attribute('aria-pressed','false')
    toggle.click()
    navigation_bundle.manifest.update(sceneId='next-city',revision=3)
    navigation_bundle.scene_id='next-city';navigation_bundle.revision=3
    (navigation_bundle.root/'manifest.json').write_text(json.dumps(navigation_bundle.manifest),encoding='utf-8')
    load(page,navigation_server,3);expect(toggle).to_have_attribute('aria-pressed','false')


@pytest.mark.parametrize('heights', [[], [None, 0, -1], [None, 0, 12]])
def test_height_notice_only_when_all_missing_and_dismisses_per_city(
        navigation_page, navigation_bundle, navigation_server, heights):
    page = navigation_page
    buildings(navigation_bundle)
    path = navigation_bundle.root / 'buildings.geojson'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['features'] = data['features'][:len(heights)]
    for feature, height in zip(data['features'], heights):
        feature['properties']['height_m'] = height
    path.write_text(json.dumps(data), encoding='utf-8')
    load(page, navigation_server, 1)
    toggle = page.get_by_role('button', name='3D 预览', exact=True)
    notice = page.locator('.preview-3d-notice')
    expect(notice).to_be_hidden()
    toggle.click()
    if not heights or any(height is not None and height > 0 for height in heights):
        expect(notice).to_be_hidden()
        return
    expect(notice).to_contain_text('未检测到建筑高度，请尝试使用最新版本的导出器重新导出。')
    notice.get_by_role('button', name='关闭', exact=True).click()
    expect(notice).to_be_hidden()
    toggle.click(); toggle.click()
    expect(notice).to_be_hidden()
    # Re-fetching building data in this city must preserve dismissal.
    navigation_bundle.manifest['revision'] = 2
    navigation_bundle.manifest['sourceRevisions']['buildings'] = 2
    navigation_bundle.manifest['sources']['buildings'] = 'buildings.geojson?revision=2'
    (navigation_bundle.root/'manifest.json').write_text(json.dumps(navigation_bundle.manifest), encoding='utf-8')
    load(page, navigation_server, 2)
    expect(notice).to_be_hidden()
    navigation_bundle.manifest.update(sceneId='height-notice-next-city', revision=3)
    (navigation_bundle.root/'manifest.json').write_text(json.dumps(navigation_bundle.manifest), encoding='utf-8')
    load(page, navigation_server, 3)
    toggle.click()
    expect(notice).to_be_visible()
    screenshot(page, 'height-notice-dismissible')
    navigation_bundle.manifest.update(sceneId='city-without-buildings', revision=4)
    del navigation_bundle.manifest['sources']['buildings']
    (navigation_bundle.root/'manifest.json').write_text(json.dumps(navigation_bundle.manifest), encoding='utf-8')
    load(page, navigation_server, 4)
    toggle.click()
    expect(notice).to_be_hidden()


def test_navigation_and_offline_share_3d(navigation_page,navigation_bundle,navigation_server,tmp_path):
    page=navigation_page; buildings(navigation_bundle,'cs2');expose_map(page)
    open_navigation(page,navigation_server);make_route(page)
    toggle=page.get_by_role('button',name='3D 预览',exact=True);toggle.click();settle_camera(page,52);drag(page,30,50)
    before=camera(page)
    page.get_by_role('button',name='模拟导航',exact=True).click()
    page.get_by_label('速度',exact=True).select_option('custom')
    page.get_by_label('速度 (km/h)',exact=True).fill('36')
    page.wait_for_timeout(1700)
    page.get_by_label('视图',exact=True).select_option('buildings')
    expect(toggle).to_have_attribute('aria-pressed','true')
    drag(page,40,20)
    expect(page.get_by_role('button',name='恢复跟随',exact=True)).to_be_visible()
    progress=float(page.locator('.navigation-simulation-progress').input_value())
    page.wait_for_timeout(400)
    assert float(page.locator('.navigation-simulation-progress').input_value())>progress
    page.get_by_role('button',name='恢复跟随',exact=True).click()
    expect(page.get_by_role('button',name='恢复跟随',exact=True)).to_be_hidden()
    toggle.click();settle_camera(page,0);assert camera(page)['pitch']==0
    assert page.get_by_label('视图',exact=True).input_value()=='flat'
    page.get_by_role('button',name='退出',exact=True).click()
    assert camera(page)==before
    expect(toggle).to_have_attribute('aria-pressed','true')
    payload=payload_from_preview_bundle(navigation_bundle)
    output=tmp_path/'3d.html'
    output.write_text(instrument(render_export_html(payload,VIEWER)),encoding='utf-8')
    requests=[];page.on('request',lambda r:requests.append(r.url) if r.url.startswith(('http:','https:')) else None)
    page.context.set_offline(True);page.goto(output.as_uri())
    expect(toggle).to_have_attribute('aria-pressed','false');expect(toggle).to_be_enabled()
    toggle.click();settle_camera(page,52);assert camera(page)['pitch']==pytest.approx(52)
    assert page.evaluate('!!__testMap.getLayer("buildings-3d")')
    screenshot(page,'height-offline-3d')
    assert not requests
    if directory := os.environ.get('CSL_PREVIEW_BROWSER_OUTPUT'):
        from pathlib import Path
        Path(directory, 'camera-transition-offline.html').write_text(output.read_text(encoding='utf-8'), encoding='utf-8')


@pytest.mark.parametrize('initial_region', [True, False])
def test_camera_buttons_animate_without_snapping_and_allow_interrupts(navigation_page, navigation_bundle, navigation_server, initial_region):
    page = navigation_page
    buildings(navigation_bundle)
    if not initial_region:
        navigation_bundle.manifest.pop('initialRegion')
        (navigation_bundle.root / 'manifest.json').write_text(json.dumps(navigation_bundle.manifest))
    expose_map(page)
    load(page, navigation_server, 1)
    page.evaluate("window.cslPreview.dispatch({type:'setOutputRotation',rotation:90})")
    page.evaluate("window.cslPreview.dispatch({type:'fitBounds'})")
    target = page.evaluate('({ ...__testMap.getCenter(), zoom:__testMap.getZoom() })')
    toggle = page.get_by_role('button', name='3D 预览', exact=True)
    toggle.click()
    page.wait_for_function('()=>__testMap.getPitch()>0 && __testMap.getPitch()<52')
    settle_camera(page, 52)
    toggle.click()
    page.wait_for_function('()=>__testMap.getPitch()>0 && __testMap.getPitch()<52')
    toggle.click()
    settle_camera(page, 52)
    # A middle drag interrupts the transition and keeps its resulting camera.
    toggle.click()
    toggle.click()
    drag(page, 20, -20)
    stopped = camera(page)
    page.wait_for_timeout(500)
    assert camera(page) == stopped
    page.evaluate('__testMap.jumpTo({center:[.0005,.0007],zoom:17,pitch:45,bearing:25})')
    start_pitch = page.get_by_role('button', name='显示全图', exact=True).evaluate('button=>{button.click();return __testMap.getPitch()}')
    assert start_pitch == pytest.approx(45)
    page.wait_for_function('()=>__testMap.getPitch()>0 && __testMap.getPitch()<45')
    settle_camera(page, 0)
    assert camera(page)['bearing'] == pytest.approx(-90)
    assert camera(page)['zoom'] == pytest.approx(target['zoom'])
    center = page.evaluate('({...__testMap.getCenter()})')
    assert center == pytest.approx({key: target[key] for key in ['lng', 'lat']})
    expect(toggle).to_have_attribute('aria-pressed', 'false')
    page.emulate_media(reduced_motion='reduce')
    toggle.click()
    assert camera(page)['pitch'] == pytest.approx(52) and not page.evaluate('__testMap.isMoving()')
    page.get_by_role('button', name='显示全图', exact=True).click()
    assert camera(page)['pitch'] == 0 and not page.evaluate('__testMap.isMoving()')


def test_camera_transitions_interrupt_navigation_entry_and_fit_from_current_view(navigation_page, navigation_bundle, navigation_server):
    page = navigation_page
    buildings(navigation_bundle)
    expose_map(page)
    open_navigation(page, navigation_server)
    make_route(page)
    page.get_by_role('button', name='模拟导航', exact=True).click()
    page.get_by_role('button', name='3D 预览', exact=True).click()
    settle_camera(page, 52)
    expect(page.get_by_role('button', name='恢复跟随', exact=True)).to_be_visible()
    values = page.get_by_role('button', name='显示全图', exact=True).evaluate('''button=>{
      const before={...__testMap.getCenter(),pitch:__testMap.getPitch(),zoom:__testMap.getZoom()};
      button.click();return {before,after:{...__testMap.getCenter(),pitch:__testMap.getPitch(),zoom:__testMap.getZoom()}};
    }''')
    assert values['after'] == pytest.approx(values['before'])
    settle_camera(page, 0)
    expect(page.locator('.navigation-car')).to_have_count(0)
