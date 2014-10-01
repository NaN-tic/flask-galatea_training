from flask import Blueprint, render_template, current_app, abort, g, \
    request, url_for, session, jsonify
from galatea.tryton import tryton
from galatea.helpers import cached
from flask.ext.paginate import Pagination
from flask.ext.babel import format_date, gettext as _, lazy_gettext as __
from datetime import datetime
import os

training = Blueprint('training', __name__, template_folder='templates')

DISPLAY_MSG = __('Displaying <b>{start} - {end}</b> {record_name} of <b>{total}</b>')

GALATEA_WEBSITE = current_app.config.get('TRYTON_GALATEA_SITE')
SHOPS = current_app.config.get('TRYTON_SALE_SHOPS')
LIMIT = current_app.config.get('TRYTON_PAGINATION_CATALOG_LIMIT', 20)

Website = tryton.pool.get('galatea.website')
Template = tryton.pool.get('product.template')
Product = tryton.pool.get('product.product')
Date = tryton.pool.get('ir.date')

TRAINING_TEMPLATE_FIELD_NAMES = [
    'name', 'esale_slug', 'esale_shortdescription', 'esale_price',
    'esale_default_images', 'esale_all_images', 'esale_new', 'esale_hot',
    'esale_metakeyword', 'training_sessions',
    ]
TRAINING_PRODUCT_FIELD_NAMES = [
    'training_start_date', 'training_end_date', 'training_registration',
    'training_place.rec_name', 'training_seats', 'training_note', 'template',
    'add_cart',
    ]

@training.route("/json/trainings", endpoint="trainings-json")
@tryton.transaction()
@cached(3500, 'trainings-json')
def training_json(lang):
    '''JSON Current Training Sessions'''

    def date_handler(obj):
        return obj.isoformat() if hasattr(obj, 'isoformat') else obj

    # Current training sessions
    domain = [
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', SHOPS),
        ('training', '=', True),
        ('training_start_date', '>=', Date.today()),
        ]
    order = [('training_start_date', 'ASC')]
    products = Product.search_read(domain, order=order, fields_names=TRAINING_PRODUCT_FIELD_NAMES)

    results = []
    for product in products:
        p = {}
        p['start_date'] = product['training_start_date'].strftime('%Y-%m-%d')
        p['end_date'] = product['training_start_date'].strftime('%Y-%m-%d')
        place = product['training_place.rec_name']
        p['place'] = place if place else ''
        template, = Template.read([product['template']],
            fields_names=TRAINING_TEMPLATE_FIELD_NAMES)
        p['name'] = template['name']
        p['url'] = '%s%s' % (current_app.config['BASE_URL'], url_for(
            'training.training', lang=g.language, slug=template['esale_slug']))
        p['shortdescription'] = template['esale_shortdescription']
        results.append(p)
    return jsonify(results=results)

@training.route("/json/<slug>", endpoint="training-detail-json")
@tryton.transaction()
@cached(3500, 'training-detail-json')
def training_detail_json(lang, slug):
    '''Training JSON Details

    slug param is a product slug or a product code
    '''
    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    products = Template.search([
        ('esale_available', '=', True),
        ('esale_slug', '=', slug),
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', SHOPS),
        ], limit=1)

    product = None
    if products:
        product, = products

    if not product:
        # search product by code
        products = Product.search([
            ('template.esale_available', '=', True),
            ('code', '=', slug),
            ('template.esale_active', '=', True),
            ('template.esale_saleshops', 'in', SHOPS),
            ], limit=1)
        if products:
            product = products[0].template

    if not product:
        abort(404)

    result = {}
    result['name'] = product.name
    result['url'] = '%s%s' % (current_app.config['BASE_URL'], url_for(
        'training.training', lang=g.language, slug=product.esale_slug))
    result['shortdescription'] = product.esale_shortdescription
    tsessions = []
    for s in product.training_sessions:
        tsession = {}
        tsession['start_date'] = s.training_start_date.strftime('%Y-%m-%d')
        tsession['end_date'] = s.training_start_date.strftime('%Y-%m-%d')
        place = s.training_place.rec_name
        tsession['place'] = place if place else ''
        tsessions.append(tsession)
    result['sessions'] = tsessions
    return jsonify(result)

@training.route("/<slug>", endpoint="training")
@tryton.transaction()
def training_detail(lang, slug):
    '''Training Details

    slug param is a product slug or a product code
    '''
    template = request.args.get('template', None)

    # template
    if template:
        blueprintdir = os.path.dirname(__file__)
        basedir = '/'.join(blueprintdir.split('/')[:-1])
        if not os.path.isfile('%s/templates/%s.html' % (basedir, template)):
            template = None
    if not template:
        template = 'training'

    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    products = Template.search([
        ('esale_available', '=', True),
        ('esale_slug', '=', slug),
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', SHOPS),
        ], limit=1)

    product = None
    if products:
        product, = products

    if not product:
        # search product by code
        products = Product.search([
            ('template.esale_available', '=', True),
            ('code', '=', slug),
            ('template.esale_active', '=', True),
            ('template.esale_saleshops', 'in', SHOPS),
            ], limit=1)
        if products:
            product = products[0].template

    if not product:
        abort(404)

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.trainings', lang=g.language),
        'name': _('Training'),
        }, {
        'slug': url_for('.all', lang=g.language),
        'name': _('All'),
        }, {
        'slug': url_for('.training', lang=g.language, slug=product.esale_slug),
        'name': product.name,
        }]

    return render_template('%s.html' % template,
            breadcrumbs=breadcrumbs,
            website=website,
            product=product,
            )

@training.route("/key/<key>", endpoint="key")
@tryton.transaction()
def keys(lang, key):
    '''Training by Key'''

    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    domain = [
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', SHOPS),
        ('training', '=', True),
        ('esale_metakeyword', 'ilike', '%'+key+'%'),
        ]
    total = Template.search_count(domain)

    offset = (page-1)*LIMIT

    products = []
    order = [('name', 'ASC')]
    templates = Template.search_read(domain, offset, LIMIT, order, TRAINING_TEMPLATE_FIELD_NAMES)
    if not templates:
        abort(404)

    for t in templates:
        template = t.copy()
        sessions = t['training_sessions']
        if sessions:
            prods = Product.read(sessions, fields_names=TRAINING_PRODUCT_FIELD_NAMES)
            template['training_sessions'] = prods # add more info in training sessions field
        products.append(template)

    pagination = Pagination(page=page, total=total, per_page=LIMIT, display_msg=DISPLAY_MSG, bs_version='3')

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.trainings', lang=g.language),
        'name': _('Training'),
        }, {
        'slug': url_for('.key', lang=g.language, key=key),
        'name': key,
        }]

    return render_template('trainings-key.html',
            breadcrumbs=breadcrumbs,
            pagination=pagination,
            products=products,
            key=key,
            )

@training.route("/all/", methods=["GET", "POST"], endpoint="all")
@tryton.transaction()
def training_all(lang):
    '''All Training'''

    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    domain_filter = session.get('training_filter', [])
    if request.form:
        domain_filter = []
        domain_filter_keys = set()
        for k, v in request.form.iteritems():
            domain_filter_keys.add(k)

        for k in list(domain_filter_keys):
            domain_filter.append((k, 'in', request.form.getlist(k)))

    session['training_filter'] = domain_filter

    domain = [
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', SHOPS),
        ('training', '=', True),
        ] + domain_filter

    total = Template.search_count(domain)
    offset = (page-1)*LIMIT

    products = []
    order = [('name', 'ASC')]
    for t in Template.search_read(domain, offset, LIMIT, order, TRAINING_TEMPLATE_FIELD_NAMES):
        template = t.copy()
        sessions = t['training_sessions']
        if sessions:
            prods = Product.read(sessions, fields_names=TRAINING_PRODUCT_FIELD_NAMES)
            template['training_sessions'] = prods # add more info in training sessions field
        products.append(template)
    pagination = Pagination(page=page, total=total, per_page=LIMIT, display_msg=DISPLAY_MSG, bs_version='3')

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.trainings', lang=g.language),
        'name': _('Training'),
        }, {
        'slug': url_for('.all', lang=g.language),
        'name': _('All'),
        }]

    return render_template('trainings-all.html',
            breadcrumbs=breadcrumbs,
            pagination=pagination,
            products=products,
            domain_filter=domain_filter,
            )

@training.route("/all/<date>", endpoint="trainings_by_date")
@tryton.transaction()
def training_list_by_date(lang, date):
    '''Training Sessions by date'''

    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    try:
        date = datetime.strptime(date, "%Y-%m-%d")
    except:
        abort(404)

    # Current training sessions
    domain = [
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', SHOPS),
        ('training', '=', True),
        ('training_start_date', '=', date),
        ]
    order = [('training_start_date', 'ASC')]
    products = Product.search_read(domain, order=order, fields_names=TRAINING_PRODUCT_FIELD_NAMES)

    templates = []
    if products:
        tpls = set()
        for product in products:
            tpls.add(product['template'])

        for t in Template.read(list(tpls), fields_names=TRAINING_TEMPLATE_FIELD_NAMES):
            template = t.copy()
            sessions = t['training_sessions']
            if sessions:
                prods = Product.read(sessions, fields_names=TRAINING_PRODUCT_FIELD_NAMES)
                template['training_sessions'] = prods # add more info in training sessions field
            templates.append(template)

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.trainings', lang=g.language),
        'name': _('Training'),
        }, {
        'slug': url_for('.all', lang=g.language),
        'name': _('Sessions')+' '+format_date(date, 'short'),
        }]

    return render_template('trainings-date.html',
            breadcrumbs=breadcrumbs,
            website=website,
            products=templates,
            date=date,
            )

@training.route("/", endpoint="trainings")
@tryton.transaction()
def training_list(lang):
    '''Current Training Sessions'''

    websites = Website.search([
        ('id', '=', GALATEA_WEBSITE),
        ], limit=1)
    if not websites:
        abort(404)
    website, = websites

    domain_filter = session.get('training_filter', [])

    # Current training sessions
    domain = [
        ('esale_available', '=', True),
        ('esale_active', '=', True),
        ('esale_saleshops', 'in', SHOPS),
        ('training', '=', True),
        ('training_start_date', '>=', Date.today()),
        ] + domain_filter
    order = [('training_start_date', 'ASC')]
    products = Product.search_read(domain, order=order, fields_names=TRAINING_PRODUCT_FIELD_NAMES)

    templates = []
    if products:
        tpls = set()
        for product in products:
            tpls.add(product['template'])

        for t in Template.read(list(tpls), fields_names=TRAINING_TEMPLATE_FIELD_NAMES):
            template = t.copy()
            sessions = t['training_sessions']
            if sessions:
                prods = Product.read(sessions, fields_names=TRAINING_PRODUCT_FIELD_NAMES)
                template['training_sessions'] = prods # add more info in training sessions field
            templates.append(template)

    #breadcumbs
    breadcrumbs = [{
        'slug': url_for('.trainings', lang=g.language),
        'name': _('Training'),
        }]

    return render_template('trainings.html',
            breadcrumbs=breadcrumbs,
            website=website,
            products=templates,
            )
