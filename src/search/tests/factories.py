""" Classes to populate test data """
import copy
from datetime import datetime


class DemoCourse:
    """ Class for dispensing demo courses """
    DEMO_COURSE_ID = "edX/DemoX/Demo_Course"
    DEMO_COURSE = {
        "start": datetime(2014, 2, 1),
        "number": "DemoX",
        "content": {
            "short_description": "Short description",
            "overview": "Long overview page",
            "display_name": "edX Demonstration Course",
            "number": "DemoX"
        },
        "course": "edX/DemoX/Demo_Course",
        "image_url": "/c4x/edX/DemoX/asset/images_course_image.jpg",
        "effort": "5:30",
        "id": DEMO_COURSE_ID,
        "enrollment_start": datetime(2014, 1, 1),
    }

    demo_course_count = 0

    @classmethod
    def get(cls, update_dict=None, remove_fields=None):
        """ get a new demo course """
        cls.demo_course_count += 1
        course_copy = copy.deepcopy(cls.DEMO_COURSE)
        if update_dict:
            if "content" in update_dict:
                course_copy["content"].update(update_dict["content"])
                del update_dict["content"]
            course_copy.update(update_dict)
        course_copy.update({"id": "{}_{}".format(course_copy["id"], cls.demo_course_count)})
        if remove_fields:
            for remove_field in remove_fields:
                if remove_field in course_copy:  # pragma: no cover
                    del course_copy[remove_field]
        return course_copy

    @classmethod
    def reset_count(cls):
        """ go back to zero """
        cls.demo_course_count = 0

    @staticmethod
    def index(searcher, course_info):
        """ Adds course info dictionary to the index """
        searcher.index(sources=course_info)

    @classmethod
    def get_and_index(cls, searcher, update_dict=None, remove_fields=None):
        """ Adds course info dictionary to the index """
        cls.index(searcher, [cls.get(update_dict, remove_fields)])
