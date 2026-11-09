const test=require('node:test');
const assert=require('node:assert/strict');
const {coverage,contains,resize,transform}=require('../mobileApp/static/mobileApp/sector-geometry.js');
const sector={id:'test',name:'Zona',shape:'rectangle',page:1,x:200,y:200,width:200,height:200,rotation:0};
const button=(x,y=200,radius=20,page=1)=>({x,y,radius,page});

test('strict majority: outside, tangent, minority, exactly half, majority and fully inside',()=>{
    assert.equal(contains(sector,button(70)),false);
    assert.equal(contains(sector,button(80)),false);
    assert.equal(contains(sector,button(99)),false);
    assert.ok(Math.abs(coverage(sector,button(100))-.5)<1e-8);
    assert.equal(contains(sector,button(100)),false);
    assert.equal(contains(sector,button(101)),true);
    assert.ok(coverage(sector,button(200))>.99999);
    assert.equal(contains(sector,button(200,200,20,2)),false);
});
test('rotation and actual triangle/circle geometry exclude empty corners of their bounding box',()=>{
    assert.equal(contains({...sector,shape:'circle'},button(110,110)),false);
    assert.equal(contains({...sector,shape:'circle'},button(200)),true);
    assert.equal(contains({...sector,shape:'triangle'},button(120,120)),false);
    assert.equal(contains({...sector,shape:'triangle'},button(200,240)),true);
    assert.equal(contains({...sector,width:260,height:30,rotation:45},button(270,270)),true);
    assert.equal(contains({...sector,width:260,height:30,rotation:45},button(270,130)),false);
});
test('a small sector centered on a large button still needs a majority of its area',()=>{
    assert.equal(contains({...sector,width:10,height:10},button(200)),false);
});
test('brush uses the union of painted strokes and excludes holes and surrounding empty space',()=>{
    const brush={...sector,shape:'brush',width:200,height:200,brush_size:.2,strokes:[[[-.4,0],[.4,0]]]};
    assert.equal(contains(brush,button(200)),true);
    assert.equal(contains(brush,button(200,260)),false);
    const one=coverage({...brush,brush_size:.05},button(200));
    const repeated=coverage({...brush,brush_size:.05,strokes:[...brush.strokes,...brush.strokes,...brush.strokes]},button(200));
    assert.ok(Math.abs(one-repeated)<1e-6);
    assert.equal(contains({...brush,brush_size:.05,strokes:[...brush.strokes,...brush.strokes,...brush.strokes]},button(200)),false);
    const dot={...brush,brush_size:.04,strokes:[[[0,0]]]};
    assert.equal(contains(dot,button(200)),false);
    const transformed={...brush,rotation:90};
    assert.equal(contains(transformed,button(200,270)),true);
    assert.equal(contains(transformed,button(270)),false);
});
test('side and corner resizing anchor the opposite edge even on rotated shapes',()=>{
    const original={...sector,width:200,height:100,rotation:37};
    const corner=transform(original,[.8,.9]);
    const changed=resize(original,'se',{x:corner[0],y:corner[1]});
    assert.ok(Math.abs(changed.width-260)<1e-8);
    assert.ok(Math.abs(changed.height-140)<1e-8);
    const fixed=transform(original,[-.5,-.5]),after=transform(changed,[-.5,-.5]);
    assert.ok(Math.hypot(fixed[0]-after[0],fixed[1]-after[1])<1e-8);
    const edge=transform(original,[.9,0]),wide=resize(original,'e',{x:edge[0],y:edge[1]});
    assert.ok(Math.abs(wide.width-280)<1e-8);
    assert.equal(wide.height,100);
    const locked=resize(original,'se',{x:corner[0],y:corner[1]},true);
    assert.equal(locked.width/locked.height,2);
    const crossed=resize(original,'e',{x:-1000,y:-1000});
    assert.ok(crossed.width>=1 && crossed.height>=1);
});
