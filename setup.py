from setuptools import setup, find_packages

setup(
    name='rapidsms-httprouter',
    version=__import__('rapidsms_httprouter').__version__,
    license="BSD",

    install_requires = [
        "rapidsms==0.9.6a",
    ],

    description='Provides HTTP endpoints for a RapidSMS router, doing all handling in the Django thread.',
    long_description=open('README.rst').read(),

    author='Nicolas Pottier, Eric Newcomer',
    author_email='code@nyaruka.com',

    url='http://github.com/nyaruka/rapidsms-httprouter',
    download_url='http://github.com/nyaruka/rapidsms-httprouter/downloads',

    include_package_data=True,

    packages=['rapidsms_httprouter'],

    zip_safe=False,
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Framework :: Django',
    ]
)
