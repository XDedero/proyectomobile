/* Geometry in a plan coordinate system whose longest side is 1000 units.
   Convex clipping measures the button's area; painted strokes are unioned so
   retracing a region never counts its area twice. No bounding-box membership. */
((root) => {
    const cross = (a, b, p) => (b[0]-a[0])*(p[1]-a[1]) - (b[1]-a[1])*(p[0]-a[0]);
    function area(polygon) {
        return Math.abs(polygon.reduce((sum, p, i) => {
            const q = polygon[(i+1)%polygon.length]; return sum + p[0]*q[1]-q[0]*p[1];
        }, 0)) / 2;
    }
    function clip(subject, boundary) {
        for (let i = 0; i < boundary.length && subject.length; i++) {
            const a = boundary[i], b = boundary[(i+1)%boundary.length], output = [];
            for (let j = 0; j < subject.length; j++) {
                const p = subject[j], q = subject[(j+1)%subject.length];
                const cp = cross(a,b,p), cq = cross(a,b,q);
                if (cp >= -1e-10) output.push(p);
                if ((cp >= 0) !== (cq >= 0)) {
                    const t = cp/(cp-cq); output.push([p[0]+t*(q[0]-p[0]),p[1]+t*(q[1]-p[1])]);
                }
            }
            subject = output;
        }
        return subject;
    }
    const ellipse = (cx,cy,rx,ry,count=192) => Array.from({length:count}, (_,i) => {
        const a = i*Math.PI*2/count; return [cx+rx*Math.cos(a),cy+ry*Math.sin(a)];
    });
    function transform(sector, p) {
        const angle = sector.rotation*Math.PI/180, x=p[0]*sector.width, y=p[1]*sector.height;
        return [sector.x+x*Math.cos(angle)-y*Math.sin(angle),sector.y+x*Math.sin(angle)+y*Math.cos(angle)];
    }
    function polygons(sector) {
        if (sector.shape !== 'brush') {
            const points = sector.shape === 'circle' ? ellipse(0,0,.5,.5) : sector.shape === 'triangle'
                ? [[0,-.5],[.5,.5],[-.5,.5]] : [[-.5,-.5],[.5,-.5],[.5,.5],[-.5,.5]];
            return [points.map(p => transform(sector,p))];
        }
        const result = [], radius = sector.brush_size/2;
        for (const stroke of sector.strokes || []) {
            for (let i=0;i<stroke.length;i++) {
                const p=stroke[i],q=stroke[Math.min(i+1,stroke.length-1)];
                if(i && i===stroke.length-1) break;
                const angle=Math.atan2(q[1]-p[1],q[0]-p[0]), points=[];
                for(let j=0;j<=24;j++) {
                    const a=angle-Math.PI/2+j*Math.PI/24;
                    points.push([q[0]+Math.cos(a)*radius,q[1]+Math.sin(a)*radius]);
                }
                for(let j=0;j<=24;j++) {
                    const a=angle+Math.PI/2+j*Math.PI/24;
                    points.push([p[0]+Math.cos(a)*radius,p[1]+Math.sin(a)*radius]);
                }
                result.push(points.map(point=>transform(sector,point)));
            }
        }
        return result;
    }
    function unionArea(polys) {
        if (!polys.length) return 0;
        if (polys.length===1) return area(polys[0]);
        const levels=[...new Set(polys.flatMap(poly=>poly.map(p=>p[1])))].sort((a,b)=>a-b);
        function width(y) {
            const intervals=[];
            for(const poly of polys) {
                const xs=[];
                for(let i=0;i<poly.length;i++) {
                    const p=poly[i],q=poly[(i+1)%poly.length];
                    if((p[1]<=y && q[1]>y)||(q[1]<=y && p[1]>y)) xs.push(p[0]+(y-p[1])*(q[0]-p[0])/(q[1]-p[1]));
                }
                if(xs.length) intervals.push([Math.min(...xs),Math.max(...xs)]);
            }
            intervals.sort((a,b)=>a[0]-b[0]);
            let total=0,start=0,end=-Infinity;
            for(const [a,b] of intervals) {
                if(a>end) { if(end!==-Infinity) total+=end-start; start=a;end=b; }
                else end=Math.max(end,b);
            }
            return total+(end===-Infinity?0:end-start);
        }
        function integrate(a,b,fa,fm,fb,whole,depth) {
            const mid=(a+b)/2,lm=width((a+mid)/2),rm=width((mid+b)/2);
            const left=(mid-a)*(fa+4*lm+fm)/6,right=(b-mid)*(fm+4*rm+fb)/6;
            if(!depth||Math.abs(left+right-whole)<1e-8) return left+right;
            return integrate(a,mid,fa,lm,fm,left,depth-1)+integrate(mid,b,fm,rm,fb,right,depth-1);
        }
        let total=0;
        for(let i=1;i<levels.length;i++) {
            const a=levels[i-1],b=levels[i],epsilon=(b-a)*1e-8;
            const fa=width(a+epsilon),fm=width((a+b)/2),fb=width(b-epsilon);
            total+=integrate(a,b,fa,fm,fb,(b-a)*(fa+4*fm+fb)/6,10);
        }
        return total;
    }
    function coverage(sector, button) {
        if(sector.page !== button.page) return 0;
        // Translate close to the origin to avoid cancellation on tiny buttons.
        const circle=ellipse(0,0,button.radius,button.radius);
        const parts=[];
        for(const polygon of polygons(sector)) {
            const local=polygon.map(p=>[p[0]-button.x,p[1]-button.y]);
            if(!local.some(p=>p[0]>=-button.radius) || !local.some(p=>p[0]<=button.radius) ||
               !local.some(p=>p[1]>=-button.radius) || !local.some(p=>p[1]<=button.radius)) continue;
            const intersection=clip(local,circle);
            if(intersection.length>=3) parts.push(intersection);
        }
        return Math.max(0,Math.min(1,unionArea(parts)/area(circle)));
    }
    function resize(sector,handle,point,keepRatio=false) {
        const directions={nw:[-1,-1],n:[0,-1],ne:[1,-1],e:[1,0],se:[1,1],s:[0,1],sw:[-1,1],w:[-1,0]};
        const direction=directions[handle];if(!direction) return sector;
        const [sx,sy]=direction,angle=sector.rotation*Math.PI/180,c=Math.cos(angle),s=Math.sin(angle);
        const dx=point.x-sector.x,dy=point.y-sector.y,u=dx*c+dy*s,v=-dx*s+dy*c;
        const anchorX=-sx*sector.width/2,anchorY=-sy*sector.height/2;
        let width=sx?Math.max(1,Math.min(2000,sx*(u-anchorX))):sector.width;
        let height=sy?Math.max(1,Math.min(2000,sy*(v-anchorY))):sector.height;
        if(keepRatio) {
            let scale=sx?width/sector.width:height/sector.height;
            if(sx&&sy&&Math.abs(height/sector.height-1)>Math.abs(scale-1)) scale=height/sector.height;
            scale=Math.max(1/Math.min(sector.width,sector.height),Math.min(2000/Math.max(sector.width,sector.height),scale));
            width=sector.width*scale;height=sector.height*scale;
        }
        const cx=sx?anchorX+sx*width/2:0,cy=sy?anchorY+sy*height/2:0;
        return {...sector,width,height,x:sector.x+cx*c-cy*s,y:sector.y+cx*s+cy*c};
    }
    const api={coverage, contains:(sector,button)=>coverage(sector,button)>.5+1e-8, polygons, transform, resize};
    if(typeof module!=='undefined') module.exports=api;
    else root.SectorGeometry=api;
})(typeof window!=='undefined'?window:globalThis);
