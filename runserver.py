#! /usr/bin/env python
# -*- coding: utf-8 -*-

# Newspipe - A web news aggregator.
# Copyright (C) 2010-2020 Cédric Bonhomme - https://www.cedricbonhomme.org
#
# For more information: https://git.sr.ht/~cedric/newspipe
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import calendar

from flask import g
from flask_babel import Babel, format_datetime
# Views
from flask_restful import Api

from newspipe.bootstrap import application

babel = Babel(application)


# Jinja filters
def month_name(month_number):
    return calendar.month_name[month_number]


application.jinja_env.filters["month_name"] = month_name
application.jinja_env.filters["datetime"] = format_datetime
# inject application in Jinja env
application.jinja_env.globals["application"] = application


with application.app_context():
    g.api = Api(application, prefix="/api/v2.0")
    g.babel = babel

    from newspipe.web import views

    application.register_blueprint(views.articles_bp)
    application.register_blueprint(views.article_bp)
    application.register_blueprint(views.feeds_bp)
    application.register_blueprint(views.feed_bp)
    application.register_blueprint(views.categories_bp)
    application.register_blueprint(views.category_bp)
    application.register_blueprint(views.icon_bp)
    application.register_blueprint(views.admin_bp)
    application.register_blueprint(views.users_bp)
    application.register_blueprint(views.user_bp)
    application.register_blueprint(views.bookmarks_bp)
    application.register_blueprint(views.bookmark_bp)


if __name__ == "__main__":
    application.run(
        host=application.config["HOST"],
        port=application.config["PORT"],
        debug=application.config["DEBUG"],
    )
