"""Meaningful geometry, provenance and refusal tests; synthetic images are not art QA."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image, ImageDraw
import frame_register as tool


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Same character scaled 2x and translated in source; shared intended pose.
        frames = []
        for name, ratio, offset in [('a', 1, 0), ('b', 2, 5)]:
            im = Image.new('RGBA', (240, 240))
            ImageDraw.Draw(im).rectangle((30*ratio+offset,20*ratio+offset,70*ratio+offset,100*ratio+offset),fill='white')
            p = self.root / f'{name}.png'; im.save(p)
            marks = {'top':[50,20],'neck':[50,40],'waist':[50,65],'bodyAxis':[50,65],'ear':[51,30],'nose':[61,32],'LeftContact':[50,100],'RightContact':[65,90]}
            frames.append(dict(id=name,source=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),markers={k:[x*ratio+offset,y*ratio+offset] for k,(x,y) in marks.items()},support='Left',swing='Right',camera={'view':'E'},targetHeadY=20,durationMs=100))
        self.data = dict(version=1,canvas=[200,140],origin=[100,100],reference='a',headMarker='top',camera={'view':'E'},proportionPairs=[['neck','waist']],anglePair=['ear','nose'],phasePairs=[['a','b']],tolerances=dict(scaleFraction=.6,proportionFraction=.05,angleDegrees=3,pairedHeadPixels=.5),frames=frames)
        self.manifest = self.root/'input.json'

    def write(self):
        self.manifest.write_text(json.dumps(self.data))

    def test_uniform_height_axis_and_ground_without_limb_warp(self):
        self.write();data,rows,errors=tool.load_plan(self.manifest)
        self.assertEqual(errors,[])
        self.assertEqual(rows[0]['scale'],1)
        self.assertEqual(rows[1]['scale'],.5)
        self.assertEqual(rows[0]['markers'],rows[1]['markers'])
        self.assertEqual(rows[1]['markers']['LeftContact'],[100,100])
        self.assertEqual(rows[1]['markers']['RightContact'],[115,90])
        self.assertEqual(tool.publish(self.manifest,self.root/'out'),0)

    def test_stale_marks_refused(self):
        self.data['frames'][0]['sha256']='0'*64;self.write()
        with self.assertRaisesRegex(ValueError,'re-annotate'):tool.load_plan(self.manifest)

    def test_projected_body_axis_can_differ_from_motion_root(self):
        self.data['targetBodyAxisX']=82
        self.write();data,rows,errors=tool.load_plan(self.manifest)
        self.assertEqual(errors,[])
        self.assertEqual(data['origin'],[100,100])
        self.assertEqual(rows[0]['markers']['bodyAxis'],[82,65])
        self.assertEqual(rows[1]['markers']['bodyAxis'],[82,65])
        self.assertEqual(rows[0]['markers']['RightContact'],[97,90])
        self.data['targetBodyAxisX']=201;self.write()
        with self.assertRaisesRegex(ValueError,'inside'):tool.load_plan(self.manifest)

    def test_visible_anchor_stabilizes_mixed_density_without_relabelling_body_axis(self):
        self.data['targetBodyAxisX']=82
        self.data['horizontalAnchor']={'marker':'neck','targetX':93}
        # A visible neck can be stable while the estimated, skirt-hidden hip differs.
        self.data['frames'][0]['markers']['bodyAxis'][0]+=4
        self.data['frames'][1]['markers']['bodyAxis'][0]+=12
        self.write();data,rows,errors=tool.load_plan(self.manifest)
        self.assertEqual(errors,[])
        self.assertEqual([r['markers']['neck'] for r in rows],[[93,40],[93,40]])
        self.assertEqual([r['bodyAxisX'] for r in rows],[97,99])
        self.assertEqual([r['targetBodyAxisX'] for r in rows],[None,None])
        self.assertEqual([r['scale'] for r in rows],[1,.5])
        self.assertEqual([r['markers']['RightContact'] for r in rows],[[108,90],[108,90]])
        self.assertEqual(tool.publish(self.manifest,self.root/'out'),0)
        report=json.loads((self.root/'out/report.json').read_text())
        self.assertEqual(report['horizontalAnchor'],{'marker':'neck','targetX':93})
        self.assertEqual(report['records'][1]['markers']['bodyAxis'],[99,65])

    def test_invalid_or_missing_horizontal_anchor_refused(self):
        for anchor in [None,[],{}, {'marker':'','targetX':93},
                       {'marker':'  ','targetX':93}, {'marker':True,'targetX':93},
                       {'marker':'absent','targetX':93}, {'marker':'neck'},
                       *[{'marker':'neck','targetX':v} for v in
                         [True,'93',float('nan'),float('inf'),-1,201]]]:
            with self.subTest(horizontalAnchor=anchor):
                self.data['horizontalAnchor']=anchor;self.write()
                with self.assertRaises(ValueError):tool.load_plan(self.manifest)
        self.data['horizontalAnchor']={'marker':'neck','targetX':93}
        del self.data['frames'][1]['markers']['neck'];self.write()
        with self.assertRaisesRegex(ValueError,'horizontalAnchor.*missing'):tool.load_plan(self.manifest)

    def test_mixed_source_density_preserves_geometry_and_proportion_checks(self):
        self.data['tolerances']['scaleFraction']=.06
        self.data['frames'][1]['pixelRatio']=2
        self.write();data,rows,errors=tool.load_plan(self.manifest)
        self.assertEqual(errors,[])
        self.assertEqual([r['pixelRatio'] for r in rows],[1,2])
        self.assertEqual([r['scale'] for r in rows],[1,.5])
        self.assertEqual([r['effectiveScaleCanonical'] for r in rows],[1,1])
        self.assertEqual(rows[1]['relativeScaleCorrection'],0)
        self.assertEqual(rows[0]['markers'],rows[1]['markers'])
        self.assertEqual(tool.publish(self.manifest,self.root/'out'),0)
        # Declared density must not disguise an actual body-proportion error.
        self.data['frames'][1]['markers']['waist'][1]+=25;self.write()
        self.assertTrue(any('proportion drift' in e for e in tool.load_plan(self.manifest)[2]))

    def test_default_density_keeps_original_scale_limit(self):
        self.data['tolerances']['scaleFraction']=.06;self.write()
        rows=tool.load_plan(self.manifest)[1]
        self.assertEqual([r['pixelRatio'] for r in rows],[1,1])
        self.assertEqual(rows[1]['relativeScaleCorrection'],-.5)
        self.assertTrue(any('scale correction exceeds limit' in e for e in tool.load_plan(self.manifest)[2]))

    def test_invalid_source_density_refused(self):
        for value in [0,-1,True,'2',float('nan'),float('inf')]:
            with self.subTest(pixelRatio=value):
                self.data['frames'][1]['pixelRatio']=value;self.write()
                with self.assertRaises(ValueError):tool.load_plan(self.manifest)

    def test_camera_mismatch_blocks_exports(self):
        self.data['frames'][1]['camera']={'view':'W'};self.write()
        self.assertEqual(tool.publish(self.manifest,self.root/'out'),2)
        self.assertFalse((self.root/'out/walk.webp').exists())

    def test_failure_blocks_previously_successful_preview(self):
        self.write();self.assertEqual(tool.publish(self.manifest,self.root/'out'),0)
        self.data['frames'][1]['camera']={'view':'wrong'};self.write()
        self.assertEqual(tool.publish(self.manifest,self.root/'out'),2)
        self.assertIn('Registration blocked',(self.root/'out/preview.html').read_text())

    def test_bad_torso_not_hidden_by_height_fit(self):
        self.data['frames'][1]['markers']['waist'][1]+=25;self.write()
        self.assertTrue(any('proportion drift' in e for e in tool.load_plan(self.manifest)[2]))

    def test_paired_phase_height_limp_detected(self):
        self.data['frames'][1]['targetHeadY']=25;self.write()
        self.assertTrue(any('asymmetric paired-phase' in e for e in tool.load_plan(self.manifest)[2]))

    def test_clipped_accessory_refused(self):
        self.data['origin'][0]=10;self.write()
        self.assertEqual(tool.publish(self.manifest,self.root/'out'),2)
        self.assertIn('clip',(self.root/'out/report.json').read_text())

    def test_nonfinite_and_role_collision(self):
        self.data['frames'][0]['markers']['neck'][0]=float('nan');self.write()
        with self.assertRaises(ValueError):tool.load_plan(self.manifest)
        self.data['frames'][0]['markers']['neck'][0]=50
        self.data['frames'][0]['swing']='Left';self.write()
        with self.assertRaisesRegex(ValueError,'roles'):tool.load_plan(self.manifest)

    def test_input_cannot_be_overwritten(self):
        self.data['frames'][0]['id']='a';self.write()
        with self.assertRaisesRegex(ValueError,'overwrite'):tool.publish(self.manifest,self.root)

    def test_annotation_export_reuses_sources_and_escapes_data(self):
        self.data['title']='</script><script>bad()</script>';self.write()
        output=self.root/'annotate.html';tool.annotation_page(self.manifest,output)
        text=output.read_text();self.assertIn('"images": ["a.png", "b.png"]',text)
        self.assertNotIn('</script><script>bad()',text)


if __name__ == '__main__':unittest.main()
