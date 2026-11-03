import unittest
from subject_preview import preview_boxes

class SubjectPreviewTests(unittest.TestCase):
    def test_center_coordinates_are_converted_and_tracking_is_preferred(self):
        target=dict(id=10,bbox=dict(x=.5,y=.5,width=.2,height=.4))
        observation=dict(tracking_box_age_s=.1,tracking_box_candidate={'targets':[target]},
                         detection_age_s=.1,candidates=[dict(id=11,bbox=target['bbox'])])
        result=preview_boxes(observation)
        self.assertFalse(result['lock_verified'])
        self.assertEqual(result['boxes'],[dict(id=10,left=.4,top=.3,width=.2,height=.4,source='tracking')])

    def test_empty_fresh_detection_clears_older_tracking_box(self):
        target=dict(id=10,bbox=dict(x=.5,y=.5,width=.2,height=.4))
        observation=dict(tracking_box_age_s=.5,tracking_box_candidate={'targets':[target]},
                         detection_age_s=.01,candidates=[],candidate_count=0)
        self.assertEqual(preview_boxes(observation)['boxes'],[])

    def test_stale_and_malformed_boxes_are_not_shown(self):
        for age,bbox in ((2,dict(x=.5,y=.5,width=.2,height=.4)),
                         (.1,dict(x=float('nan'),y=.5,width=.2,height=.4))):
            observation=dict(detection_age_s=age,candidates=[dict(id=1,bbox=bbox)])
            self.assertEqual(preview_boxes(observation)['boxes'],[])
