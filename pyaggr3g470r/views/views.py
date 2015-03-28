#! /usr/bin/env python
# -*- coding: utf-8 -*-

# pyAggr3g470r - A Web based news aggregator.
# Copyright (C) 2010-2015  Cédric Bonhomme - https://www.cedricbonhomme.org
#
# For more information : https://bitbucket.org/cedricbonhomme/pyaggr3g470r
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

__author__ = "Cedric Bonhomme"
__version__ = "$Revision: 5.3 $"
__date__ = "$Date: 2010/01/29 $"
__revision__ = "$Date: 2014/08/27 $"
__copyright__ = "Copyright (c) Cedric Bonhomme"
__license__ = "AGPLv3"

import os
import string
import random
import hashlib
import datetime
from collections import namedtuple
from bootstrap import application as app, db
from flask import render_template, request, flash, session, \
                  url_for, redirect, g, current_app, make_response, jsonify
from flask.ext.login import LoginManager, login_user, logout_user, \
                            login_required, current_user, AnonymousUserMixin
from flask.ext.principal import Principal, Identity, AnonymousIdentity, \
                                identity_changed, identity_loaded, Permission,\
                                RoleNeed, UserNeed
from flask.ext.babel import gettext
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from werkzeug import generate_password_hash

import conf
from pyaggr3g470r import utils, notifications, export
from pyaggr3g470r import controllers
from pyaggr3g470r.models import User, Feed, Article, Role
from pyaggr3g470r.decorators import feed_access_required
from pyaggr3g470r.forms import SignupForm, SigninForm, AddFeedForm, \
                    ProfileForm, InformationMessageForm, RecoverPasswordForm
from pyaggr3g470r.controllers import FeedController
if not conf.ON_HEROKU:
    import pyaggr3g470r.search as fastsearch


Principal(app)
# Create a permission with a single Need, in this case a RoleNeed.
admin_permission = Permission(RoleNeed('admin'))

login_manager = LoginManager()
login_manager.init_app(app)

#
# Management of the user's session.
#
@identity_loaded.connect_via(app)
def on_identity_loaded(sender, identity):
    # Set the identity user object
    identity.user = current_user

    # Add the UserNeed to the identity
    if hasattr(current_user, 'id'):
        identity.provides.add(UserNeed(current_user.id))

    # Assuming the User model has a list of roles, update the
    # identity with the roles that the user provides
    if hasattr(current_user, 'roles'):
        for role in current_user.roles:
            identity.provides.add(RoleNeed(role.name))

@app.before_request
def before_request():
    g.user = current_user
    if g.user.is_authenticated():
        g.user.last_seen = datetime.datetime.utcnow()
        db.session.add(g.user)
        db.session.commit()

@login_manager.user_loader
def load_user(email):
    # Return an instance of the User model
    return controllers.UserController(email).get(email=email)


#
# Custom error pages.
#
@app.errorhandler(401)
def authentication_required(e):
    flash(gettext('Authentication required.'), 'info')
    return redirect(url_for('login'))

@app.errorhandler(403)
def authentication_failed(e):
    flash(gettext('Forbidden.'), 'danger')
    return redirect(url_for('home'))

@app.errorhandler(404)
def page_not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('errors/500.html'), 500


def redirect_url(default='home'):
    return request.args.get('next') or \
            request.referrer or \
            url_for(default)

@g.babel.localeselector
def get_locale():
    """
    Called before each request to give us a chance to choose
    the language to use when producing its response.
    """
    return request.accept_languages.best_match(conf.LANGUAGES.keys())

@g.babel.timezoneselector
def get_timezone():
    try:
        return conf.TIME_ZONE[get_locale()]
    except:
        return conf.TIME_ZONE["en"]

#
# Views.
#
@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    Log in view.
    """
    if g.user is not None and g.user.is_authenticated():
        return redirect(url_for('home'))

    g.user = AnonymousUserMixin()
    form = SigninForm()

    if form.validate_on_submit():
        user = controllers.UserController(form.email.data).get(email=form.email.data)
        login_user(user)
        g.user = user
        session['email'] = form.email.data
        identity_changed.send(current_app._get_current_object(),
                              identity=Identity(user.id))
        flash(gettext("Logged in successfully."), 'success')
        return redirect(url_for('home'))
    return render_template('login.html', form=form)


@app.route('/logout')
@login_required
def logout():
    """
    Log out view. Removes the user information from the session.
    """
    session.pop('email', None)

    # Remove the user information from the session
    logout_user()

    # Remove session keys set by Flask-Principal
    for key in ('identity.name', 'identity.auth_type'):
        session.pop(key, None)

    # Tell Flask-Principal the user is anonymous
    identity_changed.send(current_app._get_current_object(), identity=AnonymousIdentity())

    flash(gettext("Logged out successfully."), 'success')
    return redirect(url_for('login'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """
    Signup page.
    """
    if int(os.environ.get("SELF_REGISTRATION", 0)) != 1:
        flash(gettext("Self-registration is disabled."), 'warning')
        return redirect(url_for('home'))
    if g.user is not None and g.user.is_authenticated():
        return redirect(url_for('home'))

    form = SignupForm()

    if form.validate_on_submit():
        role_user = Role.query.filter(Role.name == "user").first()
        user = User(nickname=form.nickname.data,
                    email=form.email.data,
                    pwdhash=generate_password_hash(form.password.data))
        user.roles = [role_user]
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            flash(gettext('Email already used.'), 'warning')
            return render_template('signup.html', form=form)

        # Send the confirmation email
        try:
            notifications.new_account_notification(user)
        except Exception as e:
            flash(gettext('Problem while sending activation email') + ': ' + str(e), 'danger')
            return redirect(url_for('home'))

        flash(gettext('Your account has been created. Check your mail to confirm it.'), 'success')
        return redirect(url_for('home'))

    return render_template('signup.html', form=form)

@app.route('/')
@login_required
def home():
    """
    Home page for connected users. Displays by default unread articles.
    """
    feeds = {feed.id: feed.title for feed in g.user.feeds}
    articles = Article.query.filter(Article.feed_id.in_(feeds.keys()),
                                    Article.user_id == g.user.id)
    filter_ = request.args.get('filter_', 'unread')
    feed_id = int(request.args.get('feed', 0))
    limit = request.args.get('limit', 1000)
    if filter_ != 'all':
        articles = articles.filter(Article.readed == (filter_ == 'read'))
    if feed_id:
        articles = articles.filter(Article.feed_id == feed_id)

    articles = articles.order_by(Article.date.desc())
    if limit != 'all':
        limit = int(limit)
        articles = articles.limit(limit)
    unread = db.session.query(Article.feed_id, func.count(Article.id))\
                       .filter(Article.readed == False, Article.user_id == g.user.id)\
                       .group_by(Article.feed_id).all()
    in_error = {feed.id: feed.error_count for feed in
                FeedController(g.user.id).read(error_count__gt=0).all()}
    def gen_url(filter_=filter_, limit=limit, feed=feed_id):
        return '?filter_=%s&limit=%s&feed=%d' % (filter_, limit, feed)
    return render_template('home.html', gen_url=gen_url, feed_id=feed_id,
                           filter_=filter_, limit=limit, feeds=feeds,
                           unread=dict(unread), articles=articles.all(),
                           in_error=in_error)


@app.route('/fetch', methods=['GET'])
@app.route('/fetch/<int:feed_id>', methods=['GET'])
@login_required
def fetch(feed_id=None):
    """
    Triggers the download of news.
    News are downloaded in a separated process, mandatory for Heroku.
    """
    if not conf.ON_HEROKU or g.user.is_admin():
        utils.fetch(g.user.id, feed_id)
        flash(gettext("Downloading articles..."), "info")
    else:
        flash(gettext("The manual retrieving of news is only available " +
                      "for administrator, on the Heroku platform."), "info")
    return redirect(redirect_url())

@app.route('/about', methods=['GET'])
def about():
    """
    'About' page.
    """
    return render_template('about.html')


@app.route('/mark_as/<string:new_value>', methods=['GET'])
@app.route('/mark_as/<string:new_value>/feed/<int:feed_id>', methods=['GET'])
@app.route('/mark_as/<string:new_value>/article/<int:article_id>', methods=['GET'])
@login_required
@feed_access_required
def mark_as(new_value='read', feed_id=None, article_id=None):
    """
    Mark all unreaded articles as read.
    """
    readed = new_value == 'read'
    articles = Article.query.filter(Article.user_id == g.user.id)
    if feed_id is not None:
        articles = articles.filter(Article.feed_id == feed_id)
        message = 'Feed marked as %s.'
    elif article_id is not None:
        articles = articles.filter(Article.id == article_id)
        message = 'Article marked as %s.'
    else:
        message = 'All article marked as %s.'
    articles.filter(Article.readed == (not readed)).update({"readed": readed})
    flash(gettext(message % new_value), 'info')
    db.session.commit()
    if readed:
        return redirect(redirect_url())
    return redirect(url_for('home'))

@app.route('/like/<int:article_id>', methods=['GET'])
@login_required
def like(article_id=None):
    """
    Mark or unmark an article as favorites.
    """
    Article.query.filter(Article.user_id == g.user.id, Article.id == article_id). \
                update({
                        "like": not Article.query.filter(Article.id == article_id).first().like
                       })
    db.session.commit()
    return redirect(redirect_url())

@app.route('/delete/<int:article_id>', methods=['GET'])
@login_required
def delete(article_id=None):
    """
    Delete an article from the database.
    """
    article = Article.query.filter(Article.id == article_id).first()
    if article is not None and article.source.subscriber.id == g.user.id:
        db.session.delete(article)
        db.session.commit()
        try:
            fastsearch.delete_article(g.user.id, article.feed_id, article.id)
        except:
            pass
        flash(gettext('Article') + ' ' + article.title + ' ' + gettext('deleted.'), 'success')
        return redirect(redirect_url())
    else:
        flash(gettext('This article do not exist.'), 'danger')
        return redirect(url_for('home'))


@app.route('/favorites', methods=['GET'])
@login_required
def favorites():
    """
    List favorites articles.
    """
    feeds_with_like = Feed.query.filter(Feed.user_id == g.user.id, Feed.articles.any(like=True))
    result, nb_favorites = [], 0
    light_feed = namedtuple('Feed', ['id', 'title', 'articles'], verbose=False, rename=False)
    for feed in feeds_with_like:
        articles = Article.query.filter(Article.user_id == g.user.id, Article.feed_id == feed.id, Article.like == True).all()
        result.append(light_feed(feed.id, feed.title, articles))
        nb_favorites += len(articles)
    return render_template('favorites.html', feeds=result, nb_favorites=nb_favorites)

@app.route('/unread/<int:feed_id>', methods=['GET'])
@app.route('/unread', methods=['GET'])
@login_required
def unread(feed_id=None):
    """
    List unread articles.
    """
    if feed_id is not None:
        feeds_with_unread = Feed.query.filter(Feed.user_id == g.user.id, Feed.id == feed_id)
    else:
        feeds_with_unread = Feed.query.filter(Feed.user_id == g.user.id, Feed.articles.any(readed=False))
    result, nb_unread = [], 0
    light_feed = namedtuple('Feed', ['id', 'title', 'articles'], verbose=False, rename=False)
    for feed in feeds_with_unread:
        articles = Article.query.filter(Article.user_id == g.user.id, Article.feed_id == feed.id, Article.readed == False).all()
        result.append(light_feed(feed.id, feed.title, articles))
        nb_unread += len(articles)
    return render_template('unread.html', feeds=result, nb_unread=nb_unread)

@app.route('/inactives', methods=['GET'])
@login_required
def inactives():
    """
    List of inactive feeds.
    """
    nb_days = int(request.args.get('nb_days', 365))
    user = controllers.UserController(g.user.email).get(email=g.user.email)
    today = datetime.datetime.now()
    inactives = []
    for feed in user.feeds:
        try:
            last_post = feed.articles[0].date
        except IndexError:
            continue
        elapsed = today - last_post
        if elapsed > datetime.timedelta(days=nb_days):
            inactives.append((feed, elapsed))
    return render_template('inactives.html', inactives=inactives, nb_days=nb_days)

@app.route('/duplicates/<int:feed_id>', methods=['GET'])
@login_required
def duplicates(feed_id=None):
    """
    Return duplicates article for a feed.
    """
    feed = Feed.query.filter(Feed.user_id == g.user.id, Feed.id == feed_id).first()
    duplicates = []
    duplicates = utils.compare_documents(feed)
    return render_template('duplicates.html', duplicates=duplicates, feed=feed)

@app.route('/index_database', methods=['GET'])
@login_required
def index_database():
    """
    Index all the database.
    """
    if not conf.ON_HEROKU:
        try:
            fastsearch.create_index(g.user.id)
            flash(gettext('Indexing database...'), 'success')
        except Exception as e:
            flash(gettext('An error occured') + ' (%s).' % e, 'danger')
        return redirect(url_for('home'))
    else:
        flash(gettext('Option not available on Heroku.'), 'success')
        return redirect(url_for('home'))

@app.route('/export', methods=['GET'])
@login_required
def export_articles():
    """
    Export all articles to HTML or JSON.
    """
    user = controllers.UserController(g.user.email).get(id=g.user.id)
    if request.args.get('format') == "HTML":
        # Export to HTML
        try:
            archive_file, archive_file_name = export.export_html(user)
        except:
            flash(gettext("Error when exporting articles."), 'danger')
            return redirect(redirect_url())
        response = make_response(archive_file)
        response.headers['Content-Type'] = 'application/x-compressed'
        response.headers['Content-Disposition'] = 'attachment; filename='+archive_file_name
    elif request.args.get('format') == "JSON":
        # Export to JSON
        try:
            json_result = export.export_json(user)
        except:
            flash(gettext("Error when exporting articles."), 'danger')
            return redirect(redirect_url())
        response = make_response(json_result)
        response.mimetype = 'application/json'
        response.headers["Content-Disposition"] = 'attachment; filename=account.json'
    else:
        flash(gettext('Export format not supported.'), 'warning')
        return redirect(redirect_url())
    return response

@app.route('/export_opml', methods=['GET'])
@login_required
def export_opml():
    """
    Export all feeds to OPML.
    """
    user = controllers.UserController(g.user.email).get(id=g.user.id)
    response = make_response(render_template('opml.xml', user=user, now=datetime.datetime.now()))
    response.headers['Content-Type'] = 'application/xml'
    response.headers['Content-Disposition'] = 'attachment; filename=feeds.opml'
    return response

@app.route('/search', methods=['GET'])
@login_required
def search():
    """
    Search articles corresponding to the query.
    """
    if conf.ON_HEROKU:
        flash(gettext("Full text search is not yet implemented for Heroku."), "warning")
        return redirect(url_for('home'))
    user = User.query.filter(User.id == g.user.id).first()

    search_result, result = [], []
    nb_articles = 0

    query = request.args.get('query', None)
    if query is not None:
        try:
            search_result, nb_articles = fastsearch.search(user.id, query)
        except Exception as e:
            flash(gettext('An error occured') + ' (%s).' % e, 'danger')
        light_feed = namedtuple('Feed', ['id', 'title', 'articles'], verbose=False, rename=False)
        for feed_id in search_result:
            for feed in user.feeds:
                if feed.id == feed_id:
                    articles = []
                    for article_id in search_result[feed_id]:
                        current_article = Article.query.filter(Article.user_id == g.user.id, Article.id == article_id).first()
                        articles.append(current_article)
                    articles = sorted(articles, key=lambda t: t.date, reverse=True)
                    result.append(light_feed(feed.id, feed.title, articles))
                    break
    return render_template('search.html', feeds=result, nb_articles=nb_articles, query=query)

@app.route('/management', methods=['GET', 'POST'])
@login_required
def management():
    """
    Display the management page.
    """
    if request.method == 'POST':
        if None != request.files.get('opmlfile', None):
            # Import an OPML file
            data = request.files.get('opmlfile', None)
            if not g.allowed_file(data.filename):
                flash(gettext('File not allowed.'), 'danger')
            else:
                try:
                    nb = utils.import_opml(g.user.email, data.read())
                    utils.fetch(g.user.email, None)
                    flash(str(nb) + '  ' + gettext('feeds imported.'), "success")
                    flash(gettext("Downloading articles..."), 'info')
                except:
                    flash(gettext("Impossible to import the new feeds."), "danger")
        elif None != request.files.get('jsonfile', None):
            # Import an account
            data = request.files.get('jsonfile', None)
            if not g.allowed_file(data.filename):
                flash(gettext('File not allowed.'), 'danger')
            else:
                try:
                    nb = utils.import_json(g.user.email, data.read())
                    flash(gettext('Account imported.'), "success")
                except:
                    flash(gettext("Impossible to import the account."), "danger")
        else:
            flash(gettext('File not allowed.'), 'danger')

    form = AddFeedForm()
    nb_feeds = len(g.user.feeds.all())
    articles = Article.query.filter(Article.user_id == g.user.id)
    nb_articles = articles.count()
    nb_unread_articles = articles.filter(Article.readed == False).count()
    return render_template('management.html', user=g.user, form=form,
                            nb_feeds=nb_feeds, nb_articles=nb_articles, nb_unread_articles=nb_unread_articles,
                            not_on_heroku = not conf.ON_HEROKU)

@app.route('/history', methods=['GET'])
@app.route('/history/<int:year>', methods=['GET'])
@app.route('/history/<int:year>/<int:month>', methods=['GET'])
@login_required
def history(year=None, month=None):
    articles_counter, articles = utils.history(g.user.id, year, month)
    return render_template('history.html',
                            articles_counter=articles_counter,
                            articles=articles,
                            year=year, month=month)

@app.route('/bookmarklet', methods=['GET'])
@app.route('/create_feed', methods=['GET', 'POST'])
@app.route('/edit_feed/<int:feed_id>', methods=['GET', 'POST'])
@login_required
@feed_access_required
def edit_feed(feed_id=None):
    """
    Add or edit a feed.
    """
    form = AddFeedForm()

    if request.method == 'POST':
        if form.validate() == False:
            return render_template('edit_feed.html', form=form)
        if feed_id is not None:
            # Edit an existing feed
            feed = FeedController(g.user.id).get(id=feed_id)
            form.populate_obj(feed)
            if feed.enabled:
                # set the error count to 0
                feed.error_count = 0
                feed.last_error = ""
            db.session.commit()
            flash(gettext('Feed successfully updated.'), 'success')
            return redirect('/edit_feed/' + str(feed_id))
        else:
            # Create a new feed
            existing_feed = [f for f in g.user.feeds if f.link == form.link.data]
            if len(existing_feed) == 0:
                new_feed = Feed(title=form.title.data, description="", link=form.link.data, \
                                site_link=form.site_link.data, enabled=form.enabled.data)
                g.user.feeds.append(new_feed)
                #user.feeds = sorted(user.feeds, key=lambda t: t.title.lower())
                db.session.commit()
                flash(gettext('Feed successfully created.'), 'success')

                utils.fetch(g.user.id, Feed.query.filter(Feed.link == form.link.data).first().id)
                flash(gettext("Downloading articles for the new feed..."), 'info')

                return redirect('/edit_feed/' + str(new_feed.id))
            else:
                flash(gettext('Feed already in the database.'), 'warning')
                return redirect('/edit_feed/' + str(existing_feed[0].id))

    if request.method == 'GET':
        if feed_id is not None:
            feed = FeedController(g.user.id).get(id=feed_id)
            form = AddFeedForm(obj=feed)
            return render_template('edit_feed.html', action=gettext("Edit the feed"), form=form, feed=feed, \
                                    not_on_heroku = not conf.ON_HEROKU)

        # Enable the user to add a feed with a bookmarklet
        if None is not request.args.get('url', None):
            existing_feed = [f for f in g.user.feeds if feed.link == request.args.get('url', None)]
            if len(existing_feed) == 0:
                g.user.feeds.append(Feed(link=request.args.get('url', None)))
                db.session.commit()
                return jsonify({"message":"ok"})
            return jsonify({"message":"Feed already in the database."})

        # Return an empty form in order to create a new feed
        return render_template('edit_feed.html', action=gettext("Add a feed"), form=form, \
                                not_on_heroku = not conf.ON_HEROKU)

@app.route('/delete_feed/<feed_id>', methods=['GET'])
@login_required
@feed_access_required
def delete_feed(feed_id=None):
    """
    Delete a feed with all associated articles.
    """
    feed = Feed.query.filter(Feed.id == feed_id).first()
    db.session.delete(feed)
    db.session.commit()
    flash(gettext('Feed') + ' ' + feed.title + ' ' + gettext('successfully deleted.'), 'success')
    return redirect(redirect_url())

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """
    Edit the profile of the currently logged user.
    """
    user = controllers.UserController(g.user.email).get(id=g.user.id)
    form = ProfileForm()

    if request.method == 'POST':
        if form.validate():
            form.populate_obj(user)
            if form.password.data != "":
                user.set_password(form.password.data)
            db.session.commit()
            flash("%s %s %s" % (gettext('User'), user.nickname,
                                gettext('successfully updated.')),
                  'success')
            return redirect(url_for('profile'))
        else:
            return render_template('profile.html', form=form)

    if request.method == 'GET':
        form = ProfileForm(obj=user)
        return render_template('profile.html', user=user, form=form)

@app.route('/delete_account', methods=['GET'])
@login_required
def delete_account():
    """
    Delete the account of the user (with all its data).
    """
    user = controllers.UserController(g.user.email).get(id=g.user.id)
    if user is not None:
        db.session.delete(user)
        db.session.commit()
        flash(gettext('Your account has been deleted.'), 'success')
    else:
        flash(gettext('This user does not exist.'), 'danger')
    return redirect(url_for('login'))

@app.route('/expire_articles', methods=['GET'])
@login_required
def expire_articles():
    """
    Delete articles older than the given number of weeks.
    """
    current_time = datetime.datetime.utcnow()
    weeks_ago = current_time - datetime.timedelta(weeks=int(request.args.get('weeks', 10)))
    articles_to_delete = Article.query.filter(User.email == g.user.email, or_(Article.date < weeks_ago, Article.retrieved_date < weeks_ago))
    for article in articles_to_delete:
        db.session.delete(article)
    flash(gettext('Articles deleted.'), 'info')
    db.session.commit()
    return redirect(redirect_url())

@app.route('/confirm_account/<string:activation_key>', methods=['GET'])
def confirm_account(activation_key=None):
    """
    Confirm the account of a user.
    """
    if activation_key != "":
        user = User.query.filter(User.activation_key == activation_key).first()
        if user is not None:
            user.activation_key = ""
            db.session.commit()
            flash(gettext('Your account has been confirmed.'), 'success')
        else:
            flash(gettext('Impossible to confirm this account.'), 'danger')
    return redirect(url_for('login'))

@app.route('/recover', methods=['GET', 'POST'])
def recover():
    """
    Enables the user to recover its account when he has forgotten
    its password.
    """
    form = RecoverPasswordForm()

    if request.method == 'POST':
        if form.validate():
            user = User.query.filter(User.email == form.email.data).first()
            characters = string.ascii_letters + string.digits
            password = "".join(random.choice(characters) for x in range(random.randint(8, 16)))
            user.set_password(password)
            db.session.commit()

            # Send the confirmation email
            try:
                notifications.new_password_notification(user, password)
                flash(gettext('New password sent to your address.'), 'success')
            except Exception as e:
                flash(gettext('Problem while sending your new password.') + ': ' + str(e), 'danger')

            return redirect(url_for('login'))
        return render_template('recover.html', form=form)

    if request.method == 'GET':
        return render_template('recover.html', form=form)

#
# Views dedicated to administration tasks.
#
@app.route('/admin/dashboard', methods=['GET', 'POST'])
@login_required
@admin_permission.require(http_exception=403)
def dashboard():
    """
    Adminstrator's dashboard.
    """
    form = InformationMessageForm()

    if request.method == 'POST':
        if form.validate():
            try:
                notifications.information_message(form.subject.data, form.message.data)
            except Exception as e:
                flash(gettext('Problem while sending email') + ': ' + str(e), 'danger')

    users = User.query.all()
    return render_template('admin/dashboard.html', users=users, current_user=g.user, form=form)

@app.route('/admin/create_user', methods=['GET', 'POST'])
@app.route('/admin/edit_user/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_permission.require(http_exception=403)
def create_user(user_id=None):
    """
    Create or edit a user.
    """
    form = ProfileForm()

    if request.method == 'POST':
        if form.validate():
            role_user = Role.query.filter(Role.name == "user").first()
            if user_id is not None:
                # Edit a user
                user = User.query.filter(User.id == user_id).first()
                form.populate_obj(user)
                if form.password.data != "":
                    user.set_password(form.password.data)
                db.session.commit()
                flash(gettext('User') + ' ' + user.nickname + ' ' + gettext('successfully updated.'), 'success')
            else:
                # Create a new user
                user = User(nickname=form.nickname.data,
                             email=form.email.data,
                             pwdhash=generate_password_hash(form.password.data))
                user.roles.extend([role_user])
                user.activation_key = ""
                db.session.add(user)
                db.session.commit()
                flash(gettext('User') + ' ' + user.nickname + ' ' + gettext('successfully created.'), 'success')
            return redirect("/admin/edit_user/"+str(user.id))
        else:
            return redirect(url_for('create_user'))

    if request.method == 'GET':
        if user_id is not None:
            user = User.query.filter(User.id == user_id).first()
            form = ProfileForm(obj=user)
            message = gettext('Edit the user') + ' <i>' + user.nickname + '</i>'
        else:
            form = ProfileForm()
            message = gettext('Add a new user')
        return render_template('/admin/create_user.html', form=form, message=message)

@app.route('/admin/user/<int:user_id>', methods=['GET'])
@login_required
@admin_permission.require(http_exception=403)
def user(user_id=None):
    """
    See information about a user (stations, etc.).
    """
    user = User.query.filter(User.id == user_id).first()
    if user is not None:
        return render_template('/admin/user.html', user=user)
    else:
        flash(gettext('This user does not exist.'), 'danger')
        return redirect(redirect_url())

@app.route('/admin/delete_user/<int:user_id>', methods=['GET'])
@login_required
@admin_permission.require(http_exception=403)
def delete_user(user_id=None):
    """
    Delete a user (with all its data).
    """
    user = User.query.filter(User.id == user_id).first()
    if user is not None:
        db.session.delete(user)
        db.session.commit()
        flash(gettext('User') + ' ' + user.nickname + ' ' + gettext('successfully deleted.'), 'success')
    else:
        flash(gettext('This user does not exist.'), 'danger')
    return redirect(redirect_url())

@app.route('/admin/enable_user/<int:user_id>', methods=['GET'])
@app.route('/admin/disable_user/<int:user_id>', methods=['GET'])
@login_required
@admin_permission.require()
def disable_user(user_id=None):
    """
    Enable or disable the account of a user.
    """
    user = User.query.filter(User.id == user_id).first()
    if user is not None:
        if user.activation_key != "":

            # Send the confirmation email
            try:
                notifications.new_account_activation(user)
                user.activation_key = ""
                flash(gettext('Account of the user') + ' ' + user.nickname + ' ' + gettext('successfully activated.'), 'success')
            except Exception as e:
                flash(gettext('Problem while sending activation email') + ': ' + str(e), 'danger')

        else:
            user.activation_key = hashlib.sha512(str(random.getrandbits(256)).encode("utf-8")).hexdigest()[:86]
            flash(gettext('Account of the user') + ' ' + user.nickname + ' ' + gettext('successfully disabled.'), 'success')
        db.session.commit()
    else:
        flash(gettext('This user does not exist.'), 'danger')
    return redirect(redirect_url())
